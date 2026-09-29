"""Browser regression for the test-page splitter; no Docker runs or production writes.

PLAYWRIGHT_BROWSERS_PATH="$PWD/.test-browsers" .test-venv/bin/python tests/smoke_splitter_browser.py
"""
import fcntl
import json
import os
import pty
import struct
import sys
import tempfile
import termios
import threading
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from server import AlgorithmServer, build_config
from docker_runner import Job
from test_docker_runner import make_algorithm
from playwright.sync_api import sync_playwright


def main():
    (PROJECT / "test-output").mkdir(exist_ok=True)
    artifact = Path(tempfile.mkdtemp(prefix="splitter-", dir=PROJECT / "test-output"))
    root = artifact / "data"; root.mkdir()
    make_algorithm(root, "algo-test")
    server = AlgorithmServer(("127.0.0.1", 0), build_config(root, min_free_bytes=0, operation_log=artifact / "operations.log"))
    server.runner.gpus = lambda: {"gpus": [], "warning": ""}
    # A real local PTY with a synthetic running job verifies browser -> API ->
    # terminal window sizing, without starting any shell or Docker container.
    master, slave = pty.openpty()
    job = Job({"path": "algo-test", "archive": "algo-test.tar", "gpu": "none"}, "bupt", "splitter-regression")
    job.fd, job.status = master, "running"
    job.append("终端布局测试：拖动分隔条调整运行文件与终端宽度。\r\n")
    server.runner.jobs[job.id] = job
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
            page.on("pageerror", lambda error: report["errors"].append(str(error)))
            def ready():
                page.locator("#test-view").wait_for(state="visible")
                page.wait_for_function("() => Runner.term !== null && document.querySelectorAll('#test-files .entry-name').length === 5")
                page.wait_for_timeout(250)
            def sizes():
                return page.evaluate("() => ({files: document.querySelector('#test-files-panel').getBoundingClientRect().width, terminal: document.querySelector('.test-terminal-panel').getBoundingClientRect().width, cols: Runner.term.cols, overflow: document.documentElement.scrollWidth > innerWidth})")
            def drag(delta):
                box = page.locator("#test-splitter").bounding_box()
                x, y = box["x"] + box["width"] / 2, box["y"] + 80
                page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x + delta, y, steps=8); page.mouse.up()
                page.wait_for_timeout(300)
                assert not page.evaluate("document.body.classList.contains('test-split-dragging')")

            page.goto(url + "/#path=algo-test&view=test"); ready()
            initial = sizes(); assert initial["files"] == 360, initial
            drag(150)
            wider = sizes()
            assert wider["files"] == initial["files"] + 150, wider
            assert wider["terminal"] == initial["terminal"] - 150, wider
            assert wider["cols"] < initial["cols"] and not wider["overflow"], wider
            assert job.cols == wider["cols"]
            _, tty_cols, _, _ = struct.unpack("HHHH", fcntl.ioctl(slave, termios.TIOCGWINSZ, b"\0" * 8))
            assert tty_cols == wider["cols"]
            assert page.locator("#test-splitter").get_attribute("aria-valuenow") == "510"
            passed("360px default; dragging expands files and shrinks/refits terminal; backend PTY dimensions follow")
            page.screenshot(path=str(artifact / "desktop-dragged.png"), full_page=True)
            page.reload(); ready(); assert sizes()["files"] == 510
            page.locator("#algorithm-list button[title='algo-test']").click()
            page.locator("#algorithm-test-button").click(); ready()
            assert sizes()["files"] == 510
            passed("width persists across refresh and file-manager/test navigation")
            drag(-1000); assert sizes()["files"] == 240
            drag(2000); assert sizes()["terminal"] == 320 and not sizes()["overflow"]
            passed("pointer capture and min/max bounds prevent panel collapse and page overflow")
            splitter = page.locator("#test-splitter")
            splitter.focus(); page.keyboard.press("Home"); assert sizes()["files"] == 240
            page.keyboard.press("ArrowRight"); assert sizes()["files"] == 260
            page.keyboard.press("ArrowLeft"); assert sizes()["files"] == 240
            page.keyboard.press("End"); assert sizes()["terminal"] == 320
            splitter.dblclick(); assert sizes()["files"] == 360
            passed("keyboard arrows/Home/End and double-click reset")
            drag(150)
            for width in (1000, 901, 900, 701):
                page.set_viewport_size({"width": width, "height": 1000}); page.wait_for_timeout(300)
                current = sizes()
                assert current["files"] >= 240 and current["terminal"] >= 320 and not current["overflow"], (width, current)
            page.set_viewport_size({"width": 390, "height": 844}); page.wait_for_timeout(300)
            assert splitter.is_hidden() and not sizes()["overflow"]
            files = page.locator("#test-files-panel").bounding_box()
            terminal = page.locator(".test-terminal-panel").bounding_box()
            assert terminal["y"] > files["y"] + files["height"]
            page.screenshot(path=str(artifact / "mobile.png"), full_page=True)
            page.set_viewport_size({"width": 1560, "height": 1000}); page.wait_for_timeout(300)
            assert sizes()["files"] == 510
            passed("responsive bounds; stacked mobile layout; desktop preference restored")
            box = splitter.bounding_box()
            page.mouse.move(box["x"] + 9, box["y"] + 80); page.mouse.down()
            assert page.evaluate("document.body.classList.contains('test-split-dragging')")
            page.evaluate("window.dispatchEvent(new Event('blur'))")
            page.mouse.up()
            assert not page.evaluate("document.body.classList.contains('test-split-dragging')")
            passed("interrupted drag releases capture and cursor state")
            # Block browser storage entirely: resize must remain functional.
            page.add_init_script("Object.defineProperty(window, 'localStorage', {get() {throw new Error('storage disabled')}})")
            page.reload(); ready(); assert sizes()["files"] == 360
            drag(60); assert sizes()["files"] == 420
            passed("disabled localStorage does not break resizing")
            assert not report["errors"], report["errors"]
            browser.close()
    finally:
        job.status, job.fd = "succeeded", None
        server.shutdown(); server.server_close(); thread.join(3)
        os.close(master); os.close(slave)
        (artifact / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print("ARTIFACTS", artifact, flush=True)


if __name__ == "__main__":
    main()
