import base64
import copy
import hashlib
import io
import json
import os
import tarfile
import tempfile
import threading
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from docker_runner import ACTIVE, DockerRunner, Job, RunnerError


def make_algorithm(root, name="algo-test"):
    folder = root / name
    folder.mkdir()
    (folder / "input").mkdir()
    (folder / "output").mkdir()
    (folder / "test_options").mkdir()
    (folder / "params.json").write_text('{"gpus":"all","shm-size":"16g"}')
    (folder / "说明.docx").write_text("document")
    (folder / "README.MD").write_text("document")
    data = json.dumps([{"Config": "a" * 64 + ".json", "RepoTags": [name + ":v1"], "Layers": []}]).encode()
    with tarfile.open(folder / (name + ".tar"), "w") as tar:
        entry = tarfile.TarInfo("manifest.json"); entry.size = len(data)
        tar.addfile(entry, io.BytesIO(data))
    return folder


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.folder = make_algorithm(self.root)
        self.logs = []
        self.runner = DockerRunner(self.root, operation=lambda *a, **kw: self.logs.append((a, kw)))
        self.runner.gpus = lambda: {"gpus": [
            {"index": "0", "uuid": "GPU-zero", "name": "Test GPU"},
            {"index": "1", "uuid": "GPU-one", "name": "Test GPU"}], "warning": ""}
        def worker(job):
            job.status = "running"
            job.cancel.wait(10)
            job.status = "stopped"
        self.runner._work = worker

    def tearDown(self):
        # Fake workers do not own actual Docker containers.
        for job in self.runner.jobs.values():
            job.cancel.set()
        for job in self.runner.jobs.values():
            if job.thread: job.thread.join(2)
        self.temp.cleanup()

    def start(self, name="algo-test", gpu="all"):
        return self.runner.start(name, name + ".tar", gpu, "bupt", "device-a")

    def test_detection_and_runtime_files(self):
        self.assertEqual(self.runner.info("algo-test")["archive"], "algo-test.tar")
        names = {e["name"] for e in self.runner.files("algo-test")["entries"]}
        self.assertEqual(names, {"input", "output", "test_options", "params.json", "algo-test.tar"})
        (self.root / "empty").mkdir()
        with self.assertRaisesRegex(RunnerError, "没有找到docker文件"):
            self.runner.info("empty")

    def test_gpu_uses_device_uuid_and_safe_mounts(self):
        plan = self.runner.plan("algo-test", "algo-test.tar", "1")
        args = self.runner.create_args(plan, "kt1-test-example")
        self.assertEqual(args[args.index("--gpus") + 1], "device=GPU-one")
        self.assertIn("NVIDIA_VISIBLE_DEVICES=GPU-one", args)
        self.assertIn("readonly", " ".join(args))
        self.assertNotIn("--privileged", args)
        self.assertNotIn("--cap-drop", args)
        self.assertNotIn("--cap-add", args)
        self.assertIn("no-new-privileges", args)
        self.assertNotIn("/var/run/docker.sock", " ".join(args))
        self.assertEqual(args[-1], "sha256:" + "a"*64)
        self.assertIn("16g", args)

    def test_cpu_and_invalid_gpu(self):
        plan = self.runner.plan("algo-test", "algo-test.tar", "none")
        self.assertNotIn("--gpus", self.runner.create_args(plan, "example"))
        for gpu in ("4", "all; touch /tmp/not-allowed", [], None):
            with self.assertRaises(RunnerError):
                self.runner.plan("algo-test", "algo-test.tar", gpu)

    def test_runtime_thread_budget_preserves_gpu_and_entrypoint(self):
        plan = self.runner.plan("algo-test", "algo-test.tar", "1")
        keys = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "OPENCV_FOR_THREADS_NUM")
        self.assertEqual(plan["environment"], dict.fromkeys(keys, "2"))
        args = self.runner.create_args(plan, "example")
        for key in keys:
            self.assertIn(key + "=2", args)
        self.assertEqual(plan["device"], "GPU-one")
        self.assertEqual(plan["container_command"], [])
        self.runner.cpu_threads = 0
        self.assertEqual(self.runner.plan("algo-test", "algo-test.tar")["environment"], {})
        for invalid in (-1, 257, "2", True):
            with self.assertRaises(ValueError):
                DockerRunner(self.root, cpu_threads=invalid)

    def test_explicit_historical_thread_budget_is_not_overwritten(self):
        history = {"options": {}, "environment": {"OMP_NUM_THREADS": "1", "EPOCHS": "1"},
                   "command": ["python", "main.py", "--epochs", "1"], "unsupported": []}
        original = copy.deepcopy(history)
        self.runner.verified["algo-test"] = history
        plan = self.runner.plan("algo-test", "algo-test.tar")
        self.assertEqual(plan["environment"]["OMP_NUM_THREADS"], "1")
        self.assertEqual(plan["environment"]["MKL_NUM_THREADS"], "2")
        self.assertEqual(plan["environment"]["EPOCHS"], "1")
        self.assertEqual(plan["container_command"], history["command"])
        self.assertEqual(history, original)

    def test_dangerous_params_rejected(self):
        for params in ({"privileged": True}, {"volume": "/:/host"}, {"env": {"NVIDIA_VISIBLE_DEVICES": "all"}},
                       {"shm-size": "1g;bad"}, {"pids-limit": -1}, {"cpus": 0}, []):
            (self.folder / "params.json").write_text(json.dumps(params))
            with self.assertRaises(RunnerError): self.runner.plan("algo-test", "algo-test.tar")

    def write_test_doc(self, command):
        from xml.sax.saxutils import escape
        with zipfile.ZipFile(self.folder / "algo-test测试说明.docx", "w") as doc:
            doc.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>' + escape(command) + '</w:t></w:r></w:p></w:body></w:document>')

    def test_docx_options_fill_missing_config(self):
        (self.folder / "params.json").write_text("{}")
        self.write_test_doc('# Linux/macOSdocker run --rm --platform linux/amd64 --shm-size=16g --gpus all --volume ./input:/app/data/input:ro --volume ./output:/app/data/output algo-test:v1')
        plan = self.runner.plan("algo-test", "algo-test.tar", "1")
        self.assertIn("linux/amd64", plan["options"])
        self.assertIn("16g", plan["options"])
        self.assertEqual(plan["device"], "GPU-one")
        self.assertEqual(len(plan["document"]["commands"]), 1)

    def test_docx_conflicts_or_unsafe_flags_fail_closed(self):
        self.write_test_doc('docker run --rm --shm-size=8g algo-test:v1')
        with self.assertRaisesRegex(RunnerError, "不一致"):
            self.runner.plan("algo-test", "algo-test.tar")
        self.write_test_doc('docker run --privileged algo-test:v1')
        with self.assertRaisesRegex(RunnerError, "尚不支持"):
            self.runner.plan("algo-test", "algo-test.tar")

    def test_oci_archive_prefers_manifest_digest(self):
        archive = self.folder / "algo-test.tar"
        with tarfile.open(archive, "a") as tar:
            data = json.dumps({"manifests": [{"digest": "sha256:" + "b"*64}]}).encode()
            entry = tarfile.TarInfo("index.json"); entry.size = len(data)
            tar.addfile(entry, io.BytesIO(data))
        self.assertEqual(self.runner.plan("algo-test", "algo-test.tar")["image"], "sha256:" + "b"*64)

    def cache_fixture(self):
        plan = self.runner.plan("algo-test", "algo-test.tar", "none")
        config = {"architecture": "amd64", "os": "linux", "created": "2026-01-01T00:00:00Z",
                  "config": {"Env": ["MODE=one"], "Cmd": ["python", "main.py"], "User": "1000", "OnBuild": None},
                  "rootfs": {"type": "layers", "diff_ids": ["sha256:" + "b" * 64]}}
        plan["_archive_config"] = (plan["image"], config)
        image = {"Id": "sha256:" + "c" * 64, "Architecture": "amd64", "Os": "linux", "Created": config["created"],
                 "Config": {k: v for k, v in config["config"].items() if v is not None},
                 "RootFS": {"Type": "layers", "Layers": config["rootfs"]["diff_ids"]}}
        return plan, config, image

    def test_cached_digest_omits_load_and_ignores_tag(self):
        plan, _, image = self.cache_fixture()
        image["Id"] = plan["image"]
        with patch.object(self.runner, "command", return_value=CompletedProcess([], 0, json.dumps([image]), "")) as cmd:
            preview = self.runner.preview(plan)
        cmd.assert_called_once_with(["image", "inspect", plan["image"]])
        self.assertTrue(preview["image_cached"])
        self.assertEqual(len(preview["commands"]), 3)
        self.assertTrue(preview["commands"][0].startswith("docker create "))
        self.assertNotIn("docker load ", "\n".join(preview["commands"]))

    def test_classic_archive_matches_containerd_without_original_tag(self):
        plan, _, image = self.cache_fixture()
        def command(args, **kwargs):
            if args == ["image", "ls", "--all", "--quiet", "--no-trunc"]:
                return CompletedProcess(args, 0, image["Id"] + "\n", "")
            if args == ["image", "inspect", image["Id"]]:
                return CompletedProcess(args, 0, json.dumps([image]), "")
            return CompletedProcess(args, 1, "[]", "Error: No such image")
        with patch.object(self.runner, "command", side_effect=command):
            preview = self.runner.preview(plan)
        self.assertTrue(preview["image_cached"])
        self.assertEqual(plan["image"], image["Id"])
        self.assertIn(image["Id"], preview["commands"][0])
        self.assertNotIn("docker load ", "\n".join(preview["commands"]))

    def test_cache_comparison_rejects_different_layers_or_runtime_config(self):
        _, config, image = self.cache_fixture()
        changes = [("Config", "Env", ["MODE=two"]), ("Config", "User", "0"),
                   ("Config", "Cmd", ["other.py"]), ("RootFS", "Layers", ["sha256:" + "d" * 64])]
        for section, key, value in changes:
            with self.subTest(key=key):
                changed = copy.deepcopy(image); changed[section][key] = value
                self.assertFalse(self.runner.same_runtime_image(config, changed))
        with_labels = copy.deepcopy(config); with_labels["config"]["Labels"] = {"version": ""}
        self.assertFalse(self.runner.same_runtime_image(with_labels, image))
        changed = copy.deepcopy(image); changed["Architecture"] = "arm64"
        self.assertFalse(self.runner.same_runtime_image(config, changed))
        self.assertTrue(self.runner.same_runtime_image(config, image))

    def test_different_image_with_same_tag_still_requires_load(self):
        plan, _, image = self.cache_fixture()
        image["RepoTags"] = plan["tags"]
        image["Config"]["Env"] = ["MODE=other"]
        def command(args, **kwargs):
            if args[:2] == ["image", "ls"]:
                return CompletedProcess(args, 0, image["Id"], "")
            if args == ["image", "inspect", image["Id"]]:
                return CompletedProcess(args, 0, json.dumps([image]), "")
            return CompletedProcess(args, 1, "[]", "No such image")
        with patch.object(self.runner, "command", side_effect=command):
            preview = self.runner.preview(plan)
        self.assertFalse(preview["image_cached"])
        self.assertTrue(preview["commands"][0].startswith("docker load --input "))

    def test_oci_config_digest_can_match_classic_store(self):
        plan, _, image = self.cache_fixture()
        config_id = plan["image"]
        plan["image"] = "sha256:" + "e" * 64
        image["Id"] = config_id
        with patch.object(self.runner, "command", side_effect=[CompletedProcess([], 1, "[]", "No such image"),
                                                               CompletedProcess([], 0, json.dumps([image]), "")]):
            self.assertTrue(self.runner.image_cached(plan))
        self.assertEqual(plan["image"], config_id)

    def test_cache_unavailable_is_not_treated_as_a_missing_image(self):
        plan, _, _ = self.cache_fixture()
        with patch.object(self.runner, "command", return_value=CompletedProcess([], 1, "", "permission denied")):
            with self.assertRaisesRegex(RunnerError, "无法检查"):
                self.runner.preview(plan)

    def test_runtime_rechecks_cache_after_preview(self):
        for preview_cached, start_cached in [(True, True), (True, False), (False, True), (False, False)]:
            with self.subTest(preview=preview_cached, start=start_cached):
                plan, _, _ = self.cache_fixture()
                cached = [preview_cached]
                def pty(job, args, **kwargs):
                    if args[0] == "load": cached[0] = True
                    return 0
                with patch.object(self.runner, "image_cached", side_effect=lambda plan: cached[0]), \
                     patch.object(self.runner, "command", return_value=CompletedProcess([], 0, "0", "")), \
                     patch.object(self.runner, "_pty", side_effect=pty) as calls, \
                     patch.object(self.runner, "_remove_owned"):
                    preview = self.runner.preview(plan)
                    self.assertEqual(preview["image_cached"], preview_cached)
                    cached[0] = start_cached
                    job = Job(plan, "bupt", "test")
                    DockerRunner._work(self.runner, job)
                self.assertEqual(job.status, "succeeded", job.error)
                self.assertEqual(sum(call.args[1][0] == "load" for call in calls.call_args_list), int(not start_cached))

    def test_archive_config_hash_is_verified(self):
        plan, config, _ = self.cache_fixture()
        raw = json.dumps(config).encode()
        digest = hashlib.sha256(raw).hexdigest()
        with tarfile.open(self.folder / "algo-test.tar", "a") as tar:
            manifest = json.dumps([{"Config": digest + ".json", "RepoTags": [], "Layers": []}]).encode()
            for name, data in [("manifest.json", manifest), (digest + ".json", raw)]:
                member = tarfile.TarInfo(name); member.size = len(data); tar.addfile(member, io.BytesIO(data))
        plan = self.runner.plan("algo-test", "algo-test.tar", "none")
        self.assertEqual(self.runner.archive_config(plan), ("sha256:" + digest, config))
        with tarfile.open(self.folder / "algo-test.tar", "a") as tar:
            member = tarfile.TarInfo(digest + ".json"); member.size = 2; tar.addfile(member, io.BytesIO(b"{}"))
        # Small tar appends can retain the padded size and occur in one filesystem
        # timestamp tick. Make this metadata-change assertion deterministic.
        archive = self.folder / "algo-test.tar"
        os.utime(archive, ns=(archive.stat().st_atime_ns, plan["fingerprint"][3] + 1_000_000_000))
        with self.assertRaisesRegex(RunnerError, "镜像文件已变化"):
            self.runner.archive_config({k: v for k, v in plan.items() if k != "_archive_config"})
        plan = self.runner.plan("algo-test", "algo-test.tar", "none")
        with self.assertRaisesRegex(RunnerError, "无法校验"):
            self.runner.archive_config(plan)

    def test_same_algorithm_atomic_across_devices(self):
        barrier = threading.Barrier(12)
        def attempt(_):
            barrier.wait()
            try: return self.start()["id"]
            except RunnerError as exc: return exc.code
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(attempt, range(12)))
        self.assertEqual(results.count("algorithm_busy"), 11)
        self.assertEqual(self.runner.list("bupt")["active"], 1)

    def test_verified_history_entrypoint_and_environment(self):
        self.runner.verified["algo-test"] = {"options": {"gpus": "device=0", "shm-size": "16g"},
            "environment": {"EPOCHS": "1"}, "command": ["python", "main.py", "--epochs", "1"],
            "record": "batch/evidence/runs/algo-test/default/run.json", "unsupported": []}
        plan = self.runner.plan("algo-test", "algo-test.tar", "1")
        args = self.runner.create_args(plan, "example")
        self.assertEqual(args[-4:], ["python", "main.py", "--epochs", "1"])
        self.assertIn("EPOCHS=1", args)
        self.assertIn("device=GPU-one", args)
        self.assertNotIn("device=0", args)
        self.assertNotIn("成功记录", "\n".join(plan["warnings"]))
        self.assertNotIn("run.json", "\n".join(plan["warnings"]))
        self.runner.verified["algo-test"]["environment"] = {"NVIDIA_VISIBLE_DEVICES": "all"}
        with self.assertRaises(RunnerError): self.runner.plan("algo-test", "algo-test.tar", "1")

    def test_failed_cleanup_keeps_algorithm_and_capacity_reserved(self):
        from subprocess import CompletedProcess
        plan = self.runner.plan("algo-test", "algo-test.tar")
        job = Job(plan, "bupt", "device-a")
        self.runner.jobs[job.id] = job
        with patch.object(self.runner, "command", return_value=CompletedProcess([], 0, "0", "")), \
             patch.object(self.runner, "image_cached", return_value=True), \
             patch.object(self.runner, "_pty", return_value=0), \
             patch.object(self.runner, "_remove_owned", side_effect=RunnerError(503, "daemon unavailable")):
            DockerRunner._work(self.runner, job)
        self.assertEqual(job.status, "cleanup_failed")
        self.assertTrue(self.runner.busy("algo-test"))
        with self.assertRaises(RunnerError): self.start()
        job.status = "failed"

    def test_parallel_limit_eight(self):
        self.assertEqual(self.runner.max_jobs, 8)
        for i in range(9): make_algorithm(self.root, f"algo-{i}")
        def attempt(i):
            try: return self.start(f"algo-{i}", str(i % 2))
            except RunnerError as exc: return exc.code
        with ThreadPoolExecutor(max_workers=9) as pool:
            results = list(pool.map(attempt, range(9)))
        self.assertEqual(sum(isinstance(r, dict) for r in results), 8)
        self.assertIn("runner_full", results)

    def test_mutations_and_output_locked_while_running(self):
        (self.folder / "output" / "keep.txt").write_text("keep")
        self.start()
        with self.assertRaises(RunnerError): self.runner.clear_output("algo-test", "bupt", "device-b", True)
        self.assertTrue((self.folder / "output" / "keep.txt").exists())
        with self.assertRaises(RunnerError):
            with self.runner.mutation("algo-test/input/file"): pass

    def test_pending_mutation_blocks_start(self):
        with self.runner.mutation("algo-test/input"):
            with self.assertRaisesRegex(RunnerError, "已有运行任务或文件操作"): self.start()
        self.assertIn("id", self.start())

    def test_clear_output_is_scoped_and_audited(self):
        outside = self.root / "outside"; outside.mkdir()
        (outside / "keep").write_text("keep")
        output = self.folder / "output"
        (output / "nested").mkdir()
        (output / "nested" / "result").write_text("result")
        (output / "link").symlink_to(outside, target_is_directory=True)
        result = self.runner.clear_output("algo-test", "bupt", "device-a", True)
        self.assertEqual(result["removed"], 3)
        self.assertEqual(list(output.iterdir()), [])
        self.assertTrue((outside / "keep").exists())
        self.assertEqual(self.logs[-1][0], ("delete", "device-a", "success"))
        self.assertEqual(self.logs[-1][1]["path"], "algo-test/output")

    def test_confirmation_and_symlink_protection(self):
        with self.assertRaises(RunnerError): self.runner.clear_output("algo-test", "bupt", "device-a", False)
        (self.folder / "output").rmdir()
        (self.folder / "output").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(RunnerError): self.runner.clear_output("algo-test", "bupt", "device-a", True)
        with self.assertRaises(RunnerError): self.runner.plan("algo-test", "algo-test.tar")
        for path in ("../outside", "/etc", "algo-test/../output", "algo-test\\input", [], None):
            with self.assertRaises(RunnerError): self.runner.algorithm(path)

    def test_job_ownership_and_reconnect_output_cursor(self):
        result = self.start()
        job = self.runner.get(result["id"], "bupt")
        with self.assertRaises(RunnerError): self.runner.get(job.id, "another-user")
        job.append("你好\r\n")
        output = job.output(0)
        self.assertEqual(base64.b64decode(output["data"]).decode(), "你好\r\n")
        self.assertEqual(job.output(output["offset"])["data"], "")
        for _ in range(40): job.append(b"x" * 65536)
        self.assertLessEqual(job.size, 2 * 1024 * 1024)
        self.assertTrue(job.output(0)["reset"])

    def test_invalid_archive_and_inputs(self):
        (self.folder / "bad.tar").write_text("not a tar")
        with self.assertRaises(RunnerError): self.runner.plan("algo-test", "bad.tar")
        with self.assertRaises(RunnerError): self.runner.plan("algo-test", "../bad.tar")
        result = self.start(); job = self.runner.get(result["id"], "bupt")
        with self.assertRaises(RunnerError): self.runner.input(job, "x"*5000)
        with self.assertRaises(RunnerError): self.runner.resize(job, 0, 100)


if __name__ == "__main__":
    unittest.main()
