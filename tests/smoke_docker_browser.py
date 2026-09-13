"""Opt-in real Docker + Chromium test. Never modifies the production unzips tree.

Requires tests/docker_fixture built as kt1-file-manager-smoke:20260914,
Playwright installed in the project test venv and its Chromium downloaded.
"""
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from server import AlgorithmServer, build_config
from playwright.sync_api import sync_playwright


def wait_for(check, timeout=90, message="condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = check()
        if value: return value
        time.sleep(.2)
    raise AssertionError("Timed out: " + message)


def main():
    artifact = Path(tempfile.mkdtemp(prefix="docker-e2e-", dir=PROJECT / "test-output"))
    root = artifact / "data"; root.mkdir()
    archive = artifact / "fixture.tar"
    subprocess.run(["docker", "save", "-o", str(archive), "kt1-file-manager-smoke:20260914"], check=True)
    for i in range(7):
        folder = root / f"smoke-{i}"; folder.mkdir()
        for name in ("input", "output", "test_options"): (folder / name).mkdir()
        (folder / "params.json").write_text('{"shm-size":"64m"}')
        (folder / "hidden.docx").write_text("not shown")
        (folder / "README.md").write_text("not shown")
        os.link(archive, folder / f"smoke-{i}.tar")
    (root / "no-docker").mkdir()
    source = Path("/home/ly/jisuanjishu_docker/kt_1/unzips/algo1-4-j-76")
    actual = root / source.name; actual.mkdir()
    shutil.copytree(source / "input", actual / "input")
    (actual / "output").mkdir()
    shutil.copy2(source / "params.json", actual / "params.json")
    shutil.copy2(source / (source.name + "测试说明.docx"), actual)
    subprocess.run(["cp", "--reflink=auto", str(source / (source.name + ".tar")), str(actual)], check=True)
    config = build_config(root, min_free_bytes=0, operation_log=artifact / "operations.log")
    server = AlgorithmServer(("127.0.0.1", 0), config)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    report = {"artifact": str(artifact), "checks": [], "page_errors": []}
    def passed(name):
        report["checks"].append(name); print("PASS", name, flush=True)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(viewport={"width": 1560, "height": 1000})
            page = context.new_page()
            page.on("pageerror", lambda exc: report["page_errors"].append(str(exc)))
            page.goto(url, wait_until="networkidle")
            page.locator("#username").fill("bupt"); page.locator("#password").fill("jsjskt1")
            page.locator("#login-button").click()
            page.locator("#app-view").wait_for(state="visible")
            def api(action, data=None, expected=200, ctx=context):
                csrf = ctx.request.get(url + "/api/session").json()["csrf"]
                response = ctx.request.post(url + "/api/tests/" + action, data=data,
                                           headers={"X-CSRF-Token": csrf}) if data is not None else ctx.request.get(url + "/api/tests/" + action)
                result = response.json()
                assert response.status == expected, (action, response.status, result)
                return result
            def jobs(): return api("jobs")["jobs"]
            def job(jid): return next(j for j in jobs() if j["id"] == jid)
            def output(jid):
                return base64.b64decode(api("output?id=" + jid + "&offset=0")["data"]).decode("utf-8", "replace")
            def open_test(path):
                page.goto(url + "/#path=" + path)
                page.locator("#algorithm-test-button").wait_for(state="visible")
                page.locator("#algorithm-test-button").click()
                page.locator("#test-view").wait_for(state="visible")
                page.wait_for_function("() => Runner.term !== null")

            page.goto(url + "/#path=no-docker")
            with page.expect_event("dialog") as event:
                page.locator("#algorithm-test-button").click()
            assert event.value.message == "没有找到docker文件"; event.value.accept()
            passed("missing Docker dialog")
            open_test("smoke-0")
            assert "全部算法" in page.locator("#test-breadcrumbs").inner_text()
            page.wait_for_function("() => document.querySelectorAll('.test-file').length === 5")
            assert "hidden.docx" not in page.locator("#test-files").inner_text()
            page.locator("#test-gpu-button").click()
            page.locator('input[name="test-gpu"][value="1"]').check()
            page.locator("#test-gpu-save").click()
            page.locator("#test-command").click()
            page.locator("#test-command-dialog").wait_for(state="visible")
            assert "device=GPU-" in page.locator("#test-command-text").inner_text()
            preview_text = page.locator("#test-command-text").inner_text()
            assert all(line.startswith("docker ") for line in preview_text.splitlines() if line.strip())
            assert page.locator("#test-command-note").count() == 0
            page.locator("#test-command-dialog .modal-footer button").click()
            page.locator("#test-start").click()
            jid = wait_for(lambda: page.evaluate("Runner.jobId"), message="job start")
            wait_for(lambda: "READY" in output(jid), message="PTY readiness")
            started = json.loads((root / "smoke-0/output/started.json").read_text())
            physical = server.runner.gpus()["gpus"]
            assert started["gpus"] == [next(g["uuid"] for g in physical if g["index"] == "1")], started
            passed("real Docker sees only physical GPU 1")
            page.locator(".xterm-helper-textarea").focus()
            page.keyboard.type("hello-world"); page.keyboard.press("Enter")
            wait_for(lambda: "RESULT: hello-world" in output(jid), message="interactive echo")
            assert (root / "smoke-0/output/result.txt").read_text() == "hello-world"
            passed("terminal keyboard -> Docker stdin -> live stdout + output file")
            messages = []
            def accept_message(dialog):
                messages.append(dialog.message); dialog.accept()
            page.once("dialog", accept_message)
            page.locator("#test-start").click()
            assert "需要先停止" in messages[-1]
            api("start", {"path": "smoke-0", "archive": "smoke-0.tar", "gpu": "0"}, expected=409)
            api("clear-output", {"path": "smoke-0", "confirmed": True}, expected=409)
            passed("frontend + backend duplicate start blocked; live output deletion blocked")
            page.screenshot(path=str(artifact / "test-page.png"), full_page=True)
            page.close()
            # A genuinely separate browser context/device can reconnect after the tab closes.
            device_b = browser.new_context(viewport={"width": 1560, "height": 1000})
            response = device_b.request.post(url + "/api/login", data={"username": "bupt", "password": "jsjskt1"})
            assert response.status == 200
            page = device_b.new_page(); page.on("pageerror", lambda exc: report["page_errors"].append(str(exc)))
            page.goto(url + f"/#path=smoke-0&view=test&job={jid}")
            page.wait_for_function("() => Runner.offset > 0")
            assert job(jid)["status"] == "running"
            passed("closed tab keeps Docker running; separate device restores terminal")
            others = []
            for i in range(1, 6):
                others.append(api("start", {"path": f"smoke-{i}", "archive": f"smoke-{i}.tar", "gpu": "0"})["job"]["id"])
            wait_for(lambda: all("READY" in output(j) for j in others), message="six real containers")
            api("start", {"path": "smoke-6", "archive": "smoke-6.tar", "gpu": "0"}, expected=429)
            for i in range(1, 6):
                gpu_data = json.loads((root / f"smoke-{i}/output/started.json").read_text())
                assert gpu_data["gpus"] == [next(g["uuid"] for g in physical if g["index"] == "0")]
            passed("six real parallel containers; GPU 0 sharing; seventh rejected")
            for other in others:
                api("stop", {"id": other})
            api("input", {"id": jid, "data": "exit\n"})
            wait_for(lambda: all(j["status"] not in ACTIVE for j in jobs()), message="stop/completion")
            assert job(jid)["status"] == "succeeded", job(jid)
            for other in others:
                assert job(other)["status"] == "stopped", job(other)
            passed("natural exit code and stop cleanup reported")
            # Confirm cancel preserves files, then confirm clears only this disposable output.
            page.wait_for_function("() => !document.querySelector('#test-clear').disabled")
            page.once("dialog", lambda dialog: dialog.dismiss())
            page.locator("#test-clear").click()
            assert (root / "smoke-0/output/result.txt").exists()
            page.once("dialog", lambda dialog: dialog.accept())
            page.locator("#test-clear").click()
            wait_for(lambda: not list((root / "smoke-0/output").iterdir()))
            passed("clear output confirmation and real deletion")
            failed = api("start", {"path": "smoke-6", "archive": "smoke-6.tar", "gpu": "none"})["job"]["id"]
            wait_for(lambda: "READY" in output(failed))
            api("input", {"id": failed, "data": "fail\n"})
            wait_for(lambda: job(failed)["status"] == "failed")
            assert job(failed)["exit_code"] == 7
            passed("nonzero Docker exit status shown as failure")
            real = api("start", {"path": source.name, "archive": source.name + ".tar", "gpu": "none"})["job"]["id"]
            wait_for(lambda: job(real)["status"] not in ACTIVE, timeout=180, message="real algorithm")
            report["real_algorithm"] = job(real)
            report["real_algorithm_output"] = output(real)
            report["result_files"] = [{"name": p.name, "size": p.stat().st_size} for p in (actual / "output").iterdir()]
            assert job(real)["status"] == "succeeded", (job(real), output(real))
            assert len(report["result_files"]) > 0
            passed("actual algo1-4-j-76 archive runs successfully and creates results")
            page.goto(url + f"/#path={source.name}&view=test&job={real}")
            page.wait_for_function("(id) => Runner.jobId === id && Runner.offset > 0", arg=real)
            page.wait_for_function("() => document.querySelector('#test-files').textContent.includes('algo1-4-j-76.tar')")
            assert "不使用 GPU" in page.locator("#test-status").inner_text()
            page.wait_for_timeout(300)
            page.screenshot(path=str(artifact / "real-algorithm.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(artifact / "mobile.png"), full_page=True)
            assert not report["page_errors"], report["page_errors"]
            passed("browser has no JavaScript errors")
            browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(3)
        (artifact / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print("ARTIFACTS", artifact, flush=True)


from docker_runner import ACTIVE
if __name__ == "__main__":
    main()
