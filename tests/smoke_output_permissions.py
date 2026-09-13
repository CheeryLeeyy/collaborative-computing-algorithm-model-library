"""Opt-in real algorithm 6/10 regression, with isolated 0775/0644 output fixtures.

Run as the normal file-manager user (not root). Production inputs, archives and
test instructions are copied; original output files and permissions are untouched.
"""
import base64
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from docker_runner import DockerRunner


def inventory(folder):
    items = {}
    for path in [folder, *sorted(folder.rglob("*"))]:
        meta = path.lstat()
        item = {"mode": stat.S_IMODE(meta.st_mode), "uid": meta.st_uid,
                "gid": meta.st_gid, "size": meta.st_size, "mtime_ns": meta.st_mtime_ns}
        if path.is_symlink():
            item["target"] = os.readlink(path)
        elif path.is_file():
            with path.open("rb") as stream:
                item["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
        items[str(path.relative_to(folder))] = item
    return items


class OldPermissionsRunner(DockerRunner):
    def create_args(self, plan, name):
        args = super().create_args(plan, name)
        return [args[0], "--cap-drop", "ALL", *args[1:]]


def main():
    assert os.getuid() != 0, "Run as the normal non-root service user"
    (PROJECT / "test-output").mkdir(exist_ok=True)
    artifact = Path(tempfile.mkdtemp(prefix="output-permissions-", dir=PROJECT / "test-output"))
    root = artifact / "data"; root.mkdir()
    production = PROJECT.parent / "unzips"
    originals = {f"algo1-4-j-{n}": inventory(production / f"algo1-4-j-{n}" / "output") for n in (6, 10)}
    report = {"artifact": str(artifact), "runs": [], "production_output_unchanged": False}
    runners = [OldPermissionsRunner(root), DockerRunner(root)]
    try:
        for algorithm in originals:
            source = production / algorithm
            target = root / algorithm; target.mkdir()
            shutil.copytree(source / "input", target / "input")
            for name in ("params.json", algorithm + "测试说明.docx", algorithm + ".tar"):
                shutil.copy2(source / name, target / name)
            output = target / "output"; output.mkdir(); output.chmod(0o775)
            assert output.stat().st_uid == os.getuid()
            result = output / "result.json"
            for existing in (False, True):
                if existing:
                    result.rename(artifact / (algorithm + "-create-result.json"))
                    result.write_text('{"permission_regression_sentinel": true}')
                    result.chmod(0o644)
                for runner, label in zip(runners, ("old-cap-drop-all", "fixed-default-capabilities")):
                    response = runner.start(algorithm, algorithm + ".tar", "none", "bupt", "permission-regression")
                    job = runner.get(response["id"], "bupt")
                    job.thread.join(90)
                    assert not job.thread.is_alive(), "Algorithm did not finish within 90 seconds"
                    text = base64.b64decode(job.output(0)["data"]).decode("utf-8", "replace")
                    log = artifact / f"{algorithm}-{'overwrite' if existing else 'create'}-{label}.log"
                    log.write_text(text)
                    record = {**job.snapshot(), "case": label, "existing_result": existing,
                              "output_mode": oct(stat.S_IMODE(output.stat().st_mode)), "log": log.name}
                    report["runs"].append(record)
                    if label == "old-cap-drop-all":
                        assert job.status == "failed" and "PermissionError" in text, record
                        if existing:
                            assert json.loads(result.read_text()) == {"permission_regression_sentinel": True}
                        else:
                            assert not result.exists()
                    else:
                        assert job.status == "succeeded" and job.exit_code == 0, (record, text)
                        data = json.loads(result.read_text())
                        assert data and "permission_regression_sentinel" not in data
                        record["result"] = data
                        assert "成功记录参考" not in text and "run.json" not in text
                        # Overwrite keeps the original service-user ownership/mode.
                        if existing:
                            assert result.stat().st_uid == os.getuid()
                            assert stat.S_IMODE(result.stat().st_mode) == 0o644
                    print("PASS", algorithm, "overwrite" if existing else "create", label, "exit", job.exit_code, flush=True)
        assert all(inventory(production / name / "output") == before for name, before in originals.items())
        report["production_output_unchanged"] = True
    finally:
        for runner in runners:
            runner.close()
        (artifact / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print("ARTIFACTS", artifact, flush=True)


if __name__ == "__main__":
    main()
