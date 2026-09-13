import base64
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
             patch.object(self.runner, "_pty", return_value=0), \
             patch.object(self.runner, "_remove_owned", side_effect=RunnerError(503, "daemon unavailable")):
            DockerRunner._work(self.runner, job)
        self.assertEqual(job.status, "cleanup_failed")
        self.assertTrue(self.runner.busy("algo-test"))
        with self.assertRaises(RunnerError): self.start()
        job.status = "failed"

    def test_parallel_limit_six(self):
        for i in range(7): make_algorithm(self.root, f"algo-{i}")
        def attempt(i):
            try: return self.start(f"algo-{i}", str(i % 2))
            except RunnerError as exc: return exc.code
        with ThreadPoolExecutor(max_workers=7) as pool:
            results = list(pool.map(attempt, range(7)))
        self.assertEqual(sum(isinstance(r, dict) for r in results), 6)
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
