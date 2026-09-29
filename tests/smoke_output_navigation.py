"""Real browser/API regression for changing output directories; no Docker required."""
import base64
import json
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from server import AlgorithmServer, build_config
from docker_runner import Job
from test_docker_runner import make_algorithm
from playwright.sync_api import sync_playwright

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aJ1sAAAAASUVORK5CYII=")


def main():
    (PROJECT / "test-output").mkdir(exist_ok=True)
    artifact = Path(tempfile.mkdtemp(prefix="output-navigation-", dir=PROJECT / "test-output"))
    root = artifact / "data"; root.mkdir()
    algorithm = make_algorithm(root)
    output = algorithm / "output"
    (output / "vis_features").mkdir()
    (output / "vis_features/before.png").write_bytes(PNG)
    original_inode = output.stat().st_ino
    server = AlgorithmServer(("127.0.0.1", 0), build_config(root, min_free_bytes=0, operation_log=artifact / "operations.log"))
    server.runner.gpus = lambda: {"gpus": [], "warning": ""}
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    report = {"checks": [], "errors": []}
    def passed(name):
        report["checks"].append(name); print("PASS", name, flush=True)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(viewport={"width": 1560, "height": 1000})
            assert context.request.post(url + "/api/login", data={"username": "bupt", "password": "jsjskt1"}).status == 200
            page = context.new_page()
            page.on("pageerror", lambda exc: report["errors"].append(str(exc)))
            requests = []
            page.on("request", lambda req: requests.append(parse_qs(urlparse(req.url).query).get("relative", [""])[0]) if "/api/tests/files?" in req.url else None)
            def at(relative):
                try:
                    page.wait_for_function("p => Runner.relative === p && Runner.filesPending === 0 && document.querySelector('#test-file-path').textContent === '/' + p", arg=relative)
                except Exception:
                    report["failure"] = page.evaluate("() => ({relative: Runner.relative, pending: Runner.filesPending, serial: Runner.filesSerial, queued: Runner.filesRefreshQueued, active: Runner.active, path: document.querySelector('#test-file-path').textContent, rows: document.querySelector('#test-files').textContent, toasts: Array.from(document.querySelectorAll('.toast')).map(t => t.textContent)})")
                    report["requests"] = requests
                    print("FAILURE", json.dumps(report, ensure_ascii=False), flush=True)
                    page.screenshot(path=str(artifact / "failure.png"))
                    raise
            def folder(name):
                page.locator("#test-files .entry-name").get_by_text(name, exact=True).click()

            page.goto(url + "/#path=algo-test&view=test")
            page.wait_for_function("() => Runner.term !== null && document.querySelectorAll('#test-files .entry-name').length === 5")
            folder("output"); at("output")
            folder("vis_features"); at("output/vis_features")
            page.once("dialog", lambda dialog: dialog.accept())
            page.locator("#test-clear").click(); at("output")
            assert not list(output.iterdir()) and output.stat().st_ino == original_inode
            passed("clear from an output subfolder returns to preserved output and discards old rows")

            # Simulate an algorithm producing intermediates, then moving them at exit.
            (output / "vis_features").mkdir()
            (output / "vis_features/before.png").write_bytes(PNG)
            job = Job({"path": "algo-test", "archive": "algo-test.tar", "gpu": "none"}, "bupt", "navigation-regression")
            job.status = "running"; job.append("running\r\n")
            server.runner.jobs[job.id] = job
            page.wait_for_function("id => Runner.jobId === id", arg=job.id)
            page.locator("#test-files-refresh").click(); at("output")
            folder("vis_features"); at("output/vis_features")
            (output / "vis_output").mkdir()
            (output / "vis_features").rename(output / "vis_output/frame_00000")
            job.status, job.exit_code = "succeeded", 0
            job.append("finished\r\n")
            at("output")
            assert page.locator("#test-files .entry-name").all_text_contents() == ["vis_output"]
            folder("vis_output"); at("output/vis_output")
            folder("frame_00000"); at("output/vis_output/frame_00000")
            folder("before.png")
            page.locator("#preview-dialog").wait_for(state="visible")
            page.wait_for_function("() => Array.from(document.querySelectorAll('#preview-dialog img')).some(img => img.complete && img.naturalWidth > 0)")
            page.locator("#preview-dialog .modal-footer .modal-close").click()
            passed("finished job refreshes final output, recovers removed intermediate path, and previews regenerated PNG")

            # Hold the first missing-child response while the same stale row is clicked twice.
            page.locator("#test-up").click(); at("output/vis_output")
            page.locator("#test-up").click(); at("output")
            (output / "vis_output").rename(artifact / "previous-output")
            (output / "fresh_output").mkdir()
            held = []
            def intercept(route):
                rel = parse_qs(urlparse(route.request.url).query).get("relative", [""])[0]
                if rel == "output/vis_output": held.append(route)
                else: route.continue_()
            page.route("**/api/tests/files?*", intercept)
            folder("vis_output"); folder("vis_output")
            assert page.evaluate("Runner.relative") == "output"
            page.evaluate("Runner.files(true)")  # Background polling must not overwrite navigation.
            assert page.evaluate("Runner.filesRefreshQueued")
            page.wait_for_timeout(100)
            assert len(held) == 2
            for route in held: route.fulfill(status=400, json={"error": "目录不存在、无权限或包含软链接", "code": "runner_error"})
            at("output")
            page.unroute("**/api/tests/files?*", intercept)
            assert page.locator("#test-files .entry-name").all_text_contents() == ["fresh_output"]
            assert not any("vis_output/vis_output" in path for path in requests), requests
            folder("fresh_output"); at("output/fresh_output")
            passed("repeated clicks on stale rows never concatenate paths; queued refresh and recovery show new folders")

            # A real permission denial also leaves a usable parent without changing permissions.
            page.locator("#test-up").click(); at("output")
            denied = output / "locked"; denied.mkdir(); denied.chmod(0)
            page.locator("#test-files-refresh").click(); at("output")
            folder("locked"); at("output")
            assert denied.stat().st_mode & 0o777 == 0
            denied.chmod(0o700)
            passed("inaccessible child recovers to parent without altering permissions")

            # Keep the old terminal attached while another device completes a new run.
            remote_job = Job({"path": "algo-test", "archive": "algo-test.tar", "gpu": "none"}, "bupt", "another-device")
            remote_job.status = "running"
            server.runner.jobs[remote_job.id] = remote_job
            page.wait_for_function("id => Runner.jobs.some(j => j.id === id && Runner.isActive(j))", arg=remote_job.id)
            folder("fresh_output"); at("output/fresh_output")
            (output / "fresh_output").rename(output / "remote_final")
            remote_job.status, remote_job.exit_code = "succeeded", 0
            at("output")
            assert page.evaluate("Runner.jobId") == job.id
            assert "remote_final" in page.locator("#test-files .entry-name").all_text_contents()
            passed("another device finishing the same algorithm refreshes files even with an older terminal attached")

            # An old request must not update the UI after leaving the test page.
            held = []
            page.route("**/api/tests/files?*", lambda route: held.append(route))
            folder("remote_final"); page.wait_for_timeout(100)
            page.locator("#algorithm-list button[title='algo-test']").click()
            page.locator("#file-manager-view").wait_for(state="visible")
            for route in held: route.fulfill(status=200, json={"entries": [], "total": 0, "page": 1})
            page.wait_for_timeout(200)
            assert page.locator("#test-view").is_hidden()
            passed("late response cannot change the current page after navigation away")
            assert not report["errors"], report["errors"]
            browser.close()
    finally:
        for job in server.runner.jobs.values(): job.status = "succeeded"
        server.shutdown(); server.server_close(); thread.join(3)
        (artifact / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print("ARTIFACTS", artifact, flush=True)


if __name__ == "__main__":
    main()
