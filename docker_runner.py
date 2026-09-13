"""Bounded Docker jobs, independent of HTTP connections; never execute a host shell."""
from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import io
import json
import os
import pty
import re
import select
import shlex
import signal
import stat
import struct
import subprocess
import tarfile
import termios
import threading
import time
import uuid
import zipfile
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path


class RunnerError(Exception):
    def __init__(self, status, message, code="runner_error"):
        super().__init__(message)
        self.status, self.message, self.code = status, message, code


ACTIVE = {"preparing", "loading", "creating", "running", "stopping", "cleanup_failed"}
ARCHIVES = (".tar", ".tar.gz", ".tgz")
DOCUMENTS = {".doc", ".docx", ".md", ".markdown", ".pdf", ".ppt", ".pptx"}
LABEL = "kt1.file-manager"


def checked_parts(path):
    if not isinstance(path, str) or not path or len(path) > 4096:
        raise RunnerError(400, "请选择算法文件夹")
    parts = path.split("/")
    if any(p in {"", ".", ".."} for p in parts) or re.search(r"[\\\x00-\x1f\x7f]", path):
        raise RunnerError(400, "路径格式无效")
    return parts


@contextlib.contextmanager
def directory(root, parts):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    except OSError as exc:
        raise RunnerError(400, "目录不存在、无权限或包含软链接") from exc
    finally:
        os.close(fd)


def read_small(fd, name, limit=65536):
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(handle, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise RunnerError(400, "配置必须是普通文件")
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise RunnerError(400, "配置文件过大")
        return data


class Job:
    def __init__(self, plan, username, client):
        self.id = uuid.uuid4().hex
        self.name = "kt1-test-" + self.id
        self.plan, self.username, self.client = plan, username, client
        self.status, self.exit_code = "preparing", None
        self.created, self.finished = time.time(), None
        self.cancel = threading.Event()
        self.lock = threading.RLock()
        self.chunks = deque()
        self.end = self.size = 0
        self.fd, self.process = None, None
        self.thread = None
        self.rows, self.cols = 30, 100
        self.error = ""

    def append(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        with self.lock:
            self.chunks.append((self.end, data))
            self.end += len(data)
            self.size += len(data)
            while self.size > 2 * 1024 * 1024 and len(self.chunks) > 1:
                self.size -= len(self.chunks.popleft()[1])

    def snapshot(self):
        with self.lock:
            return {"id": self.id, "algorithm": self.plan["path"], "archive": self.plan["archive"],
                    "gpu": self.plan["gpu"], "status": self.status, "exit_code": self.exit_code,
                    "created": self.created, "finished": self.finished, "user": self.username,
                    "client": self.client, "container": self.name, "error": self.error}

    def output(self, offset):
        with self.lock:
            start = self.chunks[0][0] if self.chunks else self.end
            reset = offset < start or offset > self.end
            offset = max(start, min(offset, self.end))
            data = b"".join(chunk[max(0, offset - pos):] for pos, chunk in self.chunks
                            if pos + len(chunk) > offset)[:128 * 1024]
            return {"data": base64.b64encode(data).decode("ascii"), "offset": offset + len(data),
                    "reset": reset, "job": self.snapshot()}


class DockerRunner:
    def __init__(self, root, max_jobs=6, operation=None, docker="docker"):
        self.root, self.max_jobs = Path(root), max_jobs
        self.operation, self.docker = operation, docker
        self.lock = threading.RLock()
        self.jobs, self.mutations = {}, set()
        self.closed = False
        self.load_lock = threading.Lock()
        self.scope = hashlib.sha256(str(self.root).encode()).hexdigest()[:16]
        manifest = Path(__file__).resolve().parent / "verified_runs.json"
        self.verified = json.loads(manifest.read_text())["algorithms"] if manifest.exists() else {}

    def command(self, args, timeout=15):
        try:
            return subprocess.run([self.docker, *args], capture_output=True, text=True,
                                  timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RunnerError(503, "Docker 不可用或响应超时，请检查服务与当前用户权限") from exc

    def gpus(self):
        try:
            result = subprocess.run(["nvidia-smi", "--query-gpu=index,uuid,name,memory.total,memory.used",
                                     "--format=csv,noheader,nounits"], capture_output=True,
                                    text=True, timeout=5, check=False)
            if result.returncode:
                return {"gpus": [], "warning": "无法读取 NVIDIA GPU，仍可选择不使用 GPU"}
            rows = []
            for line in result.stdout.splitlines():
                values = [v.strip() for v in line.split(",")]
                if len(values) == 5 and values[0].isdigit() and re.fullmatch(r"GPU-[\w-]+", values[1]):
                    rows.append(dict(zip(("index", "uuid", "name", "memory_total", "memory_used"), values)))
            return {"gpus": rows, "warning": ""}
        except (OSError, subprocess.TimeoutExpired):
            return {"gpus": [], "warning": "服务器未检测到可用 NVIDIA GPU"}

    def algorithm(self, path):
        parts = checked_parts(path)
        if len(parts) != 1:
            raise RunnerError(400, "请在顶层算法目录中启动测试")
        with directory(self.root, parts):
            pass
        return self.root / parts[0]

    def info(self, path):
        self.algorithm(path)
        with directory(self.root, [path]) as fd:
            archives = sorted(e.name for e in os.scandir(fd)
                              if e.name.lower().endswith(ARCHIVES) and e.is_file(follow_symlinks=False))
        if not archives:
            raise RunnerError(404, "没有找到docker文件", "docker_not_found")
        preferred = path + ".tar"
        return {"path": path, "archives": archives,
                "archive": preferred if preferred in archives else archives[0],
                "history": self.verified.get(path), **self.gpus()}

    def files(self, path, relative="", page=1):
        self.algorithm(path)
        parts = checked_parts(relative) if relative else []
        if any(Path(p).suffix.lower() in DOCUMENTS for p in parts):
            raise RunnerError(400, "测试页不显示说明文档")
        with directory(self.root, [path, *parts]) as fd:
            items = []
            for entry in os.scandir(fd):
                if entry.name.startswith(".algorithm-upload-") or Path(entry.name).suffix.lower() in DOCUMENTS:
                    continue
                info = entry.stat(follow_symlinks=False)
                if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                    continue
                items.append({"name": entry.name, "path": "/".join([path, *parts, entry.name]),
                              "kind": "directory" if stat.S_ISDIR(info.st_mode) else "file",
                              "size": info.st_size, "modified": info.st_mtime})
        items.sort(key=lambda x: (x["kind"] != "directory", x["name"].casefold()))
        page = max(1, page)
        return {"entries": items[(page-1)*100:page*100], "total": len(items), "page": page}

    def archive_image(self, path, archive):
        if checked_parts(archive) != [archive] or not archive.lower().endswith(ARCHIVES):
            raise RunnerError(400, "请选择 Docker 镜像归档")
        try:
            with directory(self.root, [path]) as fd:
                handle = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                with os.fdopen(handle, "rb") as stream:
                    meta = os.fstat(stream.fileno())
                    if not stat.S_ISREG(meta.st_mode):
                        raise RunnerError(400, "镜像必须是普通文件")
                    # Uncompressed Docker save archives support seek over large layer blobs.
                    # Never extract paths from an uploaded archive onto the host.
                    with tarfile.open(fileobj=stream, mode="r:*") as tar:
                        manifest = None
                        oci_index = None
                        for index, member in enumerate(tar):
                            if index > 50000:
                                raise RunnerError(400, "镜像归档条目过多")
                            if member.name in {"manifest.json", "./manifest.json"}:
                                if not member.isfile() or member.size > 1024 * 1024:
                                    raise RunnerError(400, "Docker manifest 无效")
                                manifest = json.load(tar.extractfile(member))
                            elif member.name in {"index.json", "./index.json"}:
                                if not member.isfile() or member.size > 1024 * 1024:
                                    raise RunnerError(400, "OCI index 无效")
                                oci_index = json.load(tar.extractfile(member))
                            if manifest is not None and oci_index is not None:
                                break
                        if not isinstance(manifest, list) or len(manifest) != 1:
                            raise RunnerError(400, "镜像包必须包含一个 Docker save 镜像（manifest.json）")
                        config_name = manifest[0]["Config"]
                        match = re.fullmatch(r"(?:blobs/sha256/)?([0-9a-f]{64})(?:\.json)?", config_name)
                        if not match:
                            raise RunnerError(400, "无法识别镜像配置摘要")
                        image = "sha256:" + match[1]
                        if isinstance(oci_index, dict):
                            descriptors = oci_index.get("manifests", [])
                            if len(descriptors) == 1 and re.fullmatch(r"sha256:[0-9a-f]{64}", descriptors[0].get("digest", "")):
                                image = descriptors[0]["digest"]
                        tags = manifest[0].get("RepoTags") or []
                        if not isinstance(tags, list) or any(not isinstance(t, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._/:@-]{0,255}", t) for t in tags):
                            raise RunnerError(400, "镜像标签格式无效")
                        return image, [meta.st_dev, meta.st_ino, meta.st_size, meta.st_mtime_ns], tags
        except (OSError, tarfile.TarError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise RunnerError(400, "无法读取 Docker 镜像包，请确认是 docker save 生成的完整归档") from exc

    def plan(self, path, archive, gpu="all"):
        folder = self.algorithm(path)
        if not isinstance(gpu, str):
            raise RunnerError(400, "GPU 选择无效")
        hardware = self.gpus()
        selected = next((g for g in hardware["gpus"] if gpu in {g["index"], g["uuid"]}), None)
        if gpu not in {"all", "none"} and not selected:
            raise RunnerError(400, "选择的 GPU 不存在，请刷新后重试")
        device = selected["uuid"] if selected else gpu
        warnings = []
        if gpu == "all" and not hardware["gpus"]:
            device = "none"
            warnings.append("未检测到 GPU，本次按 CPU 模式运行")
        with directory(self.root, [path]) as fd:
            try:
                params = json.loads(read_small(fd, "params.json"))
            except FileNotFoundError:
                params = {}
            except (ValueError, UnicodeError) as exc:
                raise RunnerError(400, "params.json 不是合法 JSON") from exc
            if not isinstance(params, dict):
                raise RunnerError(400, "params.json 必须是 JSON 对象")
        document = self.test_document(path)
        for key, value in document["options"].items():
            if key == "gpus":
                continue  # The explicit UI GPU visibility selection always takes precedence.
            if key in params and str(params[key]) != value:
                raise RunnerError(400, f"params.json 与测试说明.docx 的 {key} 不一致，请先核对配置")
            if key not in params:
                params[key] = value
                warnings.append(f"采用测试说明.docx 的 --{key}={value}")
        history = self.verified.get(path)
        environment, container_command = {}, []
        if history:
            if history.get("unsupported"):
                raise RunnerError(400, "历史成功命令含尚不支持的配置，请联系管理员核对")
            for key, value in history["options"].items():
                if key == "gpus":
                    continue
                if key not in params:
                    params[key] = value
                    warnings.append(f"采用历史成功记录的 --{key}={value}")
                elif str(params[key]) != str(value):
                    warnings.append(f"当前 --{key}={params[key]} 与历史记录 {value} 不同，采用当前文档/params.json")
            environment = history.get("environment", {})
            container_command = history.get("command", [])
            if not isinstance(environment, dict) or not isinstance(container_command, list):
                raise RunnerError(500, "历史运行配置格式无效")
            for key, value in environment.items():
                if (not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,100}", key) or key.startswith(("NVIDIA_", "CUDA_", "DOCKER_"))
                        or not isinstance(value, str) or len(value) > 4096 or re.search(r"[\x00-\x1f]", value)):
                    raise RunnerError(500, "历史环境配置不安全")
            if len(container_command) > 128 or any(not isinstance(v, str) or len(v) > 4096 or "\x00" in v for v in container_command):
                raise RunnerError(500, "历史容器命令格式无效")
            warnings.append("成功记录参考：" + history["record"])
            if environment or container_command:
                warnings.append("采用历史实测的容器入口/环境参数（含小规模测试设置），不代表完整训练或验收；请在运行命令中核对。")
        allowed = {"gpus", "shm-size", "memory", "cpus", "pids-limit", "platform"}
        metadata = {"algorithm", "image_size", "patch_size", "merge_r", "timing_runs",
                    "timing_warmup", "attention_dim", "output_language"}
        unknown = set(params) - allowed - metadata
        if unknown:
            raise RunnerError(400, "params.json 包含不允许的 Docker 参数：" + ", ".join(sorted(unknown)))
        if set(params) & metadata:
            warnings.append("params.json 中算法业务参数不作为 Docker CLI 参数传入")
        options = []
        for key in ("shm-size", "memory", "cpus", "pids-limit", "platform"):
            if key not in params:
                continue
            value = str(params[key])
            if key == "platform":
                if value not in {"linux/amd64", "linux/arm64", "linux/arm64/v8"}:
                    raise RunnerError(400, "不支持的容器平台：" + value)
                options.extend(["--platform", value])
                continue
            pattern = r"[1-9][0-9]*[bkmgBKMG]?" if key in {"shm-size", "memory"} else r"[0-9]+(?:\.[0-9]+)?"
            if not re.fullmatch(pattern, value) or (key in {"cpus", "pids-limit"} and float(value) <= 0):
                raise RunnerError(400, "无效的 Docker 参数：" + key)
            if key == "pids-limit" and (not value.isdigit() or int(value) > 4096):
                raise RunnerError(400, "pids-limit 应在 1 至 4096 之间")
            options.extend(["--" + key, value])
        for name in ("input", "output"):
            with directory(self.root, [path, name]):
                pass
        if any(c in str(folder) for c in (",", "\n", "\r")):
            raise RunnerError(400, "Docker 挂载路径不能包含逗号或换行")
        image, fingerprint, tags = self.archive_image(path, archive)
        return {"path": path, "archive": archive, "gpu": gpu, "device": device,
                "image": image, "fingerprint": fingerprint, "tags": tags, "options": options,
                "warnings": warnings, "folder": str(folder), "document": document,
                "history": history, "environment": environment, "container_command": container_command}

    def test_document(self, path):
        """Read-only audit of current DOCX. Extract allowlisted flags, never execute its text."""
        name = path + "测试说明.docx"
        result = {"name": name, "commands": [], "options": {}}
        with directory(self.root, [path]) as fd:
            try:
                raw = read_small(fd, name, 10 * 1024 * 1024)
            except FileNotFoundError:
                return result
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                info = archive.getinfo("word/document.xml")
                if info.file_size > 8 * 1024 * 1024:
                    raise RunnerError(400, "测试说明文档过大，无法校验运行命令")
                xml = archive.read(info)
                if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                    raise RunnerError(400, "测试说明文档包含不允许的 XML 结构")
                root = ET.fromstring(xml)
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            text = "\n".join("".join(t.text or "" for t in p.findall(".//w:t", ns))
                             for p in root.findall(".//w:p", ns))
            text = re.sub(r"\\\s*\n", " ", text)
            result["commands"] = list(dict.fromkeys(
                line[line.lower().find("docker run"):].replace("\\", " ").strip()
                for line in text.splitlines() if "docker run" in line.lower() and "`" not in line
            ))[:10]
            for command in result["commands"]:
                flags = set(re.findall(r"--([a-z][a-z-]+)", command))
                unknown = flags - {"rm", "gpus", "volume", "shm-size", "platform", "memory", "cpus", "pids-limit", "interactive", "tty"}
                if unknown:
                    raise RunnerError(400, "测试说明包含尚不支持的 Docker 参数：" + ", ".join(sorted(unknown)))
                for key in ("platform", "shm-size", "memory", "cpus", "pids-limit", "gpus"):
                    match = re.search(r"--" + key + r"(?:=|\s+)([^\s]+)", command)
                    if match:
                        value = match[1].strip("\"'")
                        if key in result["options"] and result["options"][key] != value:
                            raise RunnerError(400, "测试说明包含多组不同运行配置，请先统一：" + key)
                        result["options"][key] = value
            return result
        except (OSError, zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
            raise RunnerError(400, "测试说明.docx 无法读取，请检查文档或在文件管理中修正") from exc

    def create_args(self, plan, name):
        args = ["create", "--name", name, "--interactive", "--tty", "--init",
                "--label", f"{LABEL}={self.scope}", "--network", "bridge",
                "--security-opt", "no-new-privileges", "--cap-drop", "ALL"]
        if "--pids-limit" not in plan["options"]:
            args += ["--pids-limit", "512"]
        args += ["--mount", f"type=bind,src={plan['folder']}/input,dst=/app/data/input,readonly",
                 "--mount", f"type=bind,src={plan['folder']}/output,dst=/app/data/output"]
        if plan["device"] != "none":
            args += ["--gpus", "all" if plan["device"] == "all" else "device=" + plan["device"]]
        for key, value in plan["environment"].items():
            args += ["--env", key + "=" + value]
        args += ["--env", "NVIDIA_VISIBLE_DEVICES=" + plan["device"], *plan["options"], plan["image"], *plan["container_command"]]
        return args

    def preview(self, plan, name="kt1-test-<任务ID>"):
        commands = [[self.docker, "load", "--input", str(Path(plan["folder"]) / plan["archive"])],
                    [self.docker, *self.create_args(plan, name)],
                    [self.docker, "start", "--attach", "--interactive", name],
                    [self.docker, "rm", name]]
        return {"commands": [shlex.join(args) for args in commands], "warnings": plan["warnings"],
                "document": plan["document"],
                "history": plan["history"],
                "note": "镜像摘要已存在时跳过 load；create + start 等价于交互式 docker run。仅挂载 input（只读）和 output，使用 Docker 默认 bridge 网络。"}

    def busy(self, path):
        return any(j.plan["path"] == path and j.status in ACTIVE for j in self.jobs.values())

    @contextlib.contextmanager
    def mutation(self, path):
        top = path.split("/")[0]
        with self.lock:
            if self.busy(top) or top in self.mutations:
                raise RunnerError(409, "该算法正在测试或修改，请停止/等待结束后再修改文件", "algorithm_busy")
            self.mutations.add(top)
        try:
            yield
        finally:
            with self.lock:
                self.mutations.discard(top)

    def start(self, path, archive, gpu, username, client):
        self.algorithm(path)
        # Reserve before validating/reading archives, so concurrent requests cannot oversubscribe.
        with self.lock:
            if self.closed:
                raise RunnerError(503, "服务正在停止")
            if self.busy(path) or path in self.mutations:
                raise RunnerError(409, "该算法目录已有运行任务或文件操作", "algorithm_busy")
            if sum(j.status in ACTIVE for j in self.jobs.values()) >= self.max_jobs:
                raise RunnerError(429, f"最多同时运行 {self.max_jobs} 个任务，请等待或停止已有任务", "runner_full")
            self.mutations.add(path)
            # Validation is outside the lock; the final capacity check and insertion are atomic.
        try:
            plan = self.plan(path, archive, gpu)
            with self.lock:
                if self.closed or sum(j.status in ACTIVE for j in self.jobs.values()) >= self.max_jobs:
                    raise RunnerError(429, "并行任务已满，请稍后重试", "runner_full")
                job = Job(plan, username, client)
                self.jobs[job.id] = job
                completed = [j for j in self.jobs.values() if j.status not in ACTIVE]
                for old in completed[:-50]:
                    del self.jobs[old.id]
                job.thread = threading.Thread(target=self._work, args=(job,), daemon=True,
                                              name="docker-" + job.id[:8])
                job.thread.start()
                return job.snapshot()
        finally:
            with self.lock:
                self.mutations.discard(path)

    def get(self, job_id, username):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or job.username != username:
                raise RunnerError(404, "任务不存在或已过期")
            return job

    def list(self, username):
        with self.lock:
            return {"jobs": [j.snapshot() for j in reversed(list(self.jobs.values())) if j.username == username],
                    "max_jobs": self.max_jobs, "active": sum(j.status in ACTIVE for j in self.jobs.values())}

    def _phase(self, job, status):
        with job.lock:
            job.status = "stopping" if job.cancel.is_set() else status

    def _pty(self, job, args, interactive=False):
        job.append("\r\n$ " + shlex.join([self.docker, *args]) + "\r\n")
        master, slave = pty.openpty()
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", job.rows, job.cols, 0, 0))
            process = subprocess.Popen([self.docker, *args], stdin=slave, stdout=slave, stderr=slave,
                                       start_new_session=True, close_fds=True)
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)
        os.set_blocking(master, False)
        with job.lock:
            job.fd, job.process = master, process
        try:
            eof = False
            cancelled_at = None
            while not eof:
                ready, _, _ = select.select([master], [], [], 0.2)
                if ready:
                    try:
                        data = os.read(master, 65536)
                        if data:
                            job.append(data)
                        else:
                            eof = True
                    except BlockingIOError:
                        pass
                    except OSError:
                        eof = True
                elif process.poll() is not None:
                    break
                if job.cancel.is_set() and not interactive and process.poll() is None:
                    if cancelled_at is None:
                        cancelled_at = time.monotonic()
                        process.terminate()
                    elif time.monotonic() - cancelled_at > 3:
                        process.kill()
            return process.wait(timeout=10)
        finally:
            with job.lock:
                job.fd, job.process = None, None
            os.close(master)
            if process.poll() is None:
                process.kill()
                process.wait()

    def _work(self, job):
        rc = 1
        try:
            plan = job.plan
            job.append("算法测试 " + plan["path"] + "\r\n" + "\r\n".join(plan["warnings"]) + "\r\n")
            while not self.load_lock.acquire(timeout=0.2):
                if job.cancel.is_set():
                    return
            try:
                if job.cancel.is_set():
                    return
                # Serialize loads: archives can share tags, but jobs always run immutable image IDs.
                if self.command(["image", "inspect", plan["image"]]).returncode:
                    self._phase(job, "loading")
                    previous = {}
                    for tag in plan["tags"]:
                        old = self.command(["image", "inspect", "--format", "{{.Id}}", tag])
                        if old.returncode == 0:
                            previous[tag] = old.stdout.strip()
                    try:
                        if self._pty(job, ["load", "--input", str(Path(plan["folder"]) / plan["archive"])]):
                            raise RunnerError(400, "Docker 镜像加载失败，请查看终端输出")
                        if job.cancel.is_set():
                            return
                        if self.command(["image", "inspect", plan["image"]]).returncode:
                            # Resolve legacy archives imported into Docker 29's containerd
                            # store AFTER load, then pin the immutable ID, never a tag.
                            for tag in plan["tags"]:
                                loaded = self.command(["image", "inspect", "--format", "{{.Id}}", tag])
                                if loaded.returncode == 0 and re.fullmatch(r"sha256:[0-9a-f]{64}", loaded.stdout.strip()):
                                    plan["image"] = loaded.stdout.strip()
                                    break
                    finally:
                        # Loading an uploaded archive must not replace another service's
                        # existing tags. Our container runs the newly loaded immutable ID.
                        for tag, old_id in previous.items():
                            restored = self.command(["tag", old_id, tag])
                            if restored.returncode:
                                job.append("\r\n警告：原镜像标签恢复失败 " + tag + "\r\n")
                if self.command(["image", "inspect", plan["image"]]).returncode:
                    raise RunnerError(400, "加载后的镜像摘要与归档不一致")
            finally:
                self.load_lock.release()
            if job.cancel.is_set():
                return
            # Recheck archive and mounted directories immediately before container creation.
            with directory(self.root, [plan["path"]]) as fd:
                meta = os.stat(plan["archive"], dir_fd=fd, follow_symlinks=False)
                if [meta.st_dev, meta.st_ino, meta.st_size, meta.st_mtime_ns] != plan["fingerprint"]:
                    raise RunnerError(409, "镜像文件已变化，请重新开始")
            for name in ("input", "output"):
                with directory(self.root, [plan["path"], name]):
                    pass
            self._phase(job, "creating")
            args = self.create_args(plan, job.name)
            job.append("\r\n$ " + shlex.join([self.docker, *args]) + "\r\n")
            result = self.command(args, timeout=60)
            if result.returncode:
                job.append(result.stderr + "\r\n")
                raise RunnerError(400, "容器创建失败")
            if job.cancel.is_set():
                return
            self._phase(job, "running")
            rc = self._pty(job, ["start", "--attach", "--interactive", job.name], interactive=True)
            result = self.command(["inspect", "--format", "{{.State.ExitCode}}", job.name])
            if result.returncode == 0:
                rc = int(result.stdout.strip())
        except Exception as exc:
            job.error = str(exc)
            job.append("\r\n错误：" + str(exc) + "\r\n")
        finally:
            # Only this job's UUID container, with our project label, may be removed.
            cleanup_failed = False
            try:
                self._remove_owned(job)
            except Exception as exc:
                cleanup_failed = True
                job.error = (job.error + "; 容器清理失败：" + str(exc)).strip("; ")
                job.append("\r\n" + job.error + "\r\n")
            with job.lock:
                job.exit_code = rc
                job.status = "cleanup_failed" if cleanup_failed else "stopped" if job.cancel.is_set() else "succeeded" if rc == 0 else "failed"
                job.finished = None if cleanup_failed else time.time()
            job.append(f"\r\n[任务{job.status}，退出码 {rc}]\r\n")

    def _owned(self, job):
        result = self.command(["inspect", "--format", '{{index .Config.Labels "' + LABEL + '"}}', job.name])
        if result.returncode:
            if "no such object" in result.stderr.lower() or "no such container" in result.stderr.lower():
                return False
            raise RunnerError(503, "无法确认容器状态，请检查 Docker 服务后重试停止")
        if result.stdout.strip() != self.scope:
            raise RunnerError(409, "同名容器不属于本项目，不会操作该容器")
        return True

    def _remove_owned(self, job):
        if self._owned(job):
            result = self.command(["rm", "--force", job.name], timeout=25)
            if result.returncode:
                raise RunnerError(503, result.stderr.strip()[:300])

    def stop(self, job):
        with job.lock:
            retry_cleanup = job.status == "cleanup_failed"
            if job.status not in ACTIVE:
                return job.snapshot()
            if job.cancel.is_set() and not retry_cleanup:
                return job.snapshot()
            job.cancel.set()
            job.status = "stopping"
        def stop_container():
            if retry_cleanup:
                try:
                    self._remove_owned(job)
                    with job.lock:
                        job.status, job.finished = "stopped", time.time()
                except Exception as exc:
                    job.error = str(exc)
                    job.status = "cleanup_failed"
                return
            # Retry covers the small gap between create and start; do not terminate an unrelated CLI.
            while job.thread and job.thread.is_alive():
                try:
                    if self._owned(job):
                        self.command(["stop", "--timeout", "3", job.name], timeout=10)
                except RunnerError:
                    pass
                time.sleep(0.2)
        threading.Thread(target=stop_container, daemon=True).start()
        return job.snapshot()

    def input(self, job, data):
        if not isinstance(data, str) or len(data.encode("utf-8")) > 4096:
            raise RunnerError(400, "终端输入过长")
        with job.lock:
            if job.status != "running" or job.fd is None:
                raise RunnerError(409, "容器尚未就绪或已结束")
            try:
                payload = data.encode("utf-8")
                # Nonblocking PTYs must not tie up a request indefinitely.
                written = os.write(job.fd, payload)
                if written != len(payload):
                    raise RunnerError(429, "终端输入缓冲区已满，请稍后重试")
            except OSError as exc:
                raise RunnerError(409, "终端连接已关闭或暂时忙碌") from exc

    def resize(self, job, rows, cols):
        if type(rows) is not int or type(cols) is not int or not (5 <= rows <= 200 and 20 <= cols <= 400):
            raise RunnerError(400, "终端尺寸无效")
        with job.lock:
            job.rows, job.cols = rows, cols
            if job.fd is not None:
                try:
                    fcntl.ioctl(job.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
                    if job.process and job.process.poll() is None:
                        os.kill(job.process.pid, signal.SIGWINCH)
                except OSError:
                    pass

    def clear_output(self, path, username, client, confirmed=False):
        if confirmed is not True:
            raise RunnerError(400, "请先确认清空 output")
        self.algorithm(path)
        removed = 0
        def remove(fd, name):
            nonlocal removed
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    for entry in os.scandir(child):
                        remove(child, entry.name)
                finally:
                    os.close(child)
                os.rmdir(name, dir_fd=fd)
            else:
                os.unlink(name, dir_fd=fd)
            removed += 1
        try:
            with self.mutation(path), directory(self.root, [path, "output"]) as fd:
                for entry in os.scandir(fd):
                    remove(fd, entry.name)
        except Exception as exc:
            if self.operation:
                self.operation("delete", client, "failed", username=username, path=path + "/output",
                               detail=f"clear_output; removed={removed}; {exc}")
            if isinstance(exc, RunnerError):
                raise
            raise RunnerError(403, f"清空失败（已删除 {removed} 项），请检查 output 的文件权限") from exc
        if self.operation:
            self.operation("delete", client, "success", username=username, path=path + "/output",
                           detail=f"clear_output; removed={removed}; kept_directory=true")
        return {"ok": True, "removed": removed}

    def close(self):
        with self.lock:
            self.closed = True
            active = [job for job in self.jobs.values() if job.status in ACTIVE]
        for job in active:
            self.stop(job)
        deadline = time.monotonic() + 20
        for job in active:
            if job.thread:
                job.thread.join(max(0, deadline - time.monotonic()))
