"""Opt-in destructive web acceptance test: clears each selected algorithm's output.

All starts, clears, GPU choices and stops go through actual Chromium UI controls.
Only algorithms 1..131 with Docker archives are eligible. No input, image, or
algorithm source is edited. Evidence and CSV are written outside the data root.
"""
import argparse
import asyncio
import base64
import csv
import json
import os
import re
import stat
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from playwright.async_api import async_playwright

PROJECT = Path(__file__).resolve().parents[1]
ACTIVE = {"preparing", "loading", "creating", "running", "stopping", "cleanup_failed"}
ARCHIVES = (".tar", ".tar.gz", ".tgz")
FIELDS = {
    "number": "算法编号", "algorithm": "算法", "archive": "镜像包", "gpu": "GPU",
    "status": "测试结论", "job_status": "任务状态", "exit_code": "退出码",
    "cleared": "已清空output", "removed": "清空删除项数", "started": "开始时间",
    "finished": "结束时间", "seconds": "耗时秒", "output_files": "新输出文件数",
    "output_bytes": "新输出字节数", "nonempty_files": "非空文件数", "new_output": "有新输出",
    "browsable": "网页目录可打开", "preview": "网页预览", "cached": "跳过镜像加载",
    "job_id": "任务ID", "error": "错误摘要", "notes": "备注", "evidence": "证据目录",
}


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def save_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def inventory(root):
    """Metadata only, do not follow any symlink or read output file contents."""
    result = {"files": [], "directories": [], "links": [], "errors": []}
    if not root.exists() or root.is_symlink():
        result["errors"].append("output 不存在或为软链接")
        return result
    def walk_error(exc):
        result["errors"].append(str(exc))
    for base, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        for name in dirs + files:
            path = Path(base) / name
            try:
                info = path.lstat()
                relative = str(path.relative_to(root))
                if stat.S_ISLNK(info.st_mode):
                    result["links"].append(relative)
                elif stat.S_ISDIR(info.st_mode):
                    result["directories"].append(relative)
                elif stat.S_ISREG(info.st_mode):
                    result["files"].append({"path": relative, "size": info.st_size, "mtime_ns": info.st_mtime_ns})
            except OSError as exc:
                walk_error(exc)
    return result


class Batch:
    def __init__(self, args):
        self.args = args
        self.root = args.root.resolve(strict=True)
        self.artifact = args.report.resolve()
        if self.artifact.exists():
            raise RuntimeError("报告目录已存在，请使用新目录以免覆盖历史测试")
        if self.artifact.is_relative_to(self.root):
            raise RuntimeError("报告目录不能位于算法数据根目录内")
        self.artifact.mkdir(parents=True)
        self.rows = {}
        self.session = None
        self.context = None
        self.running = set()
        self.peak_active = 0
        self.peak_server_active = 0
        self.stage = "preflight"

    def persist(self):
        rows = [self.rows[n] for n in sorted(self.rows)]
        csv_path = self.artifact / "results.csv"
        temporary = csv_path.with_suffix(".csv.tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(FIELDS))
            writer.writerow(FIELDS)
            writer.writerows({k: row.get(k, "") for k in FIELDS} for row in rows)
        temporary.replace(csv_path)
        save_json(self.artifact / "results.json", rows)
        save_json(self.artifact / "progress.json", {
            "updated": now(), "stage": self.stage,
            "counts": dict(Counter(r["status"] for r in rows)),
            "running": sorted(self.running), "peak_active": self.peak_active,
            "peak_server_active": self.peak_server_active,
        })

    async def api(self, action):
        response = await self.context.request.get(self.args.url + "/api/tests/" + action, timeout=120000)
        payload = await response.json()
        if response.status != 200:
            raise RuntimeError(f"GET {action}: HTTP {response.status}: {payload}")
        return payload

    async def enter(self, page, name, relative):
        await page.locator("#test-files .entry-name").get_by_text(name, exact=True).click()
        await page.wait_for_function(
            "p => Runner.relative === p && Runner.filesPending === 0 && document.querySelector('#test-file-path').textContent === '/' + p",
            arg=relative, timeout=30000)

    async def browse_output(self, page, row, after):
        await page.locator("#test-files-refresh").click()
        await page.wait_for_function("() => Runner.filesPending === 0", timeout=30000)
        await self.enter(page, "output", "output")
        row["browsable"] = True
        choices = sorted(after["files"], key=lambda f: (Path(f["path"]).suffix.lower() not in {".png", ".jpg", ".jpeg", ".json", ".txt", ".csv"}, len(Path(f["path"]).parts), f["path"]))
        if not choices:
            row["preview"] = "无输出文件"
            return
        parts = Path(choices[0]["path"]).parts
        relative = "output"
        for part in parts[:-1]:
            relative += "/" + part
            await self.enter(page, part, relative)
        file = page.locator("#test-files .entry-name").get_by_text(parts[-1], exact=True)
        while not await file.count():
            next_button = page.locator("#test-files-next")
            if await next_button.is_disabled():
                raise RuntimeError("新输出文件没有出现在网页目录列表")
            await next_button.click()
            await page.wait_for_function("() => Runner.filesPending === 0")
        await file.click()
        await page.locator("#preview-dialog").wait_for(state="visible")
        if Path(parts[-1]).suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            await page.wait_for_function("() => Array.from(document.querySelectorAll('#preview-dialog img')).some(i => i.complete && i.naturalWidth > 0)")
            row["preview"] = "图片预览成功：" + choices[0]["path"]
        else:
            await page.wait_for_timeout(300)
            row["preview"] = "预览弹窗已打开：" + choices[0]["path"]
        await page.locator("#preview-dialog .modal-footer .modal-close").click()

    async def run_one(self, number, gpu):
        row = self.rows[number]
        if self.args.allow_active_tests:
            row["status"] = "waiting_capacity"
            self.persist()
            while True:
                live = await self.api("jobs")
                info = await self.api("info?" + urlencode({"path": row["algorithm"]}))
                devices = [g for g in info["gpus"] if gpu == "all" or g["index"] == gpu]
                free = all(int(g["memory_total"]) - int(g["memory_used"]) >= self.args.min_free_gpu_mib for g in devices)
                if live["active"] < live["max_jobs"] and devices and free:
                    break
                await asyncio.sleep(5)
        evidence = self.artifact / row["algorithm"]
        evidence.mkdir()
        row.update(status="preparing", gpu=gpu, started=now(), evidence=str(evidence))
        self.running.add(number)
        self.peak_active = max(self.peak_active, len(self.running))
        self.persist()
        print(now(), "BEGIN", row["algorithm"], "GPU", gpu, flush=True)
        page = await self.context.new_page()
        page.set_default_timeout(120000)
        dialogs, page_errors = [], []
        async def dialog_handler(dialog):
            dialogs.append({"type": dialog.type, "message": dialog.message})
            if dialog.type == "confirm" and (row["algorithm"] + "/output" in dialog.message or "停止 " + row["algorithm"] in dialog.message):
                await dialog.accept()
            else:
                await dialog.dismiss()
        page.on("dialog", dialog_handler)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        job = None
        output = self.root / row["algorithm"] / "output"
        start_time = time.monotonic()
        try:
            # No API shortcuts for the actions being tested.
            await page.goto(self.args.url + "/#" + urlencode({"path": row["algorithm"]}))
            await page.locator("#algorithm-test-button").click()
            await page.locator("#test-view").wait_for(state="visible")
            await page.wait_for_function("() => Runner.term !== null && Runner.filesPending === 0")
            await page.locator("#test-archive").select_option(row["archive"])
            live = await self.api("jobs")
            if any(j["algorithm"] == row["algorithm"] and j["status"] in ACTIVE for j in live["jobs"]):
                raise RuntimeError("已有其他设备任务，本轮不清空或停止它")
            if output.is_symlink() or output.resolve() != self.root / row["algorithm"] / "output":
                raise RuntimeError("output 路径不是预期的实体目录，不进行清空")
            save_json(evidence / "before_inventory.json", await asyncio.to_thread(inventory, output))
            async with page.expect_response(lambda r: r.url.endswith("/api/tests/clear-output") and r.request.method == "POST") as response_info:
                await page.locator("#test-clear").click()
            response = await response_info.value
            clear = await response.json()
            save_json(evidence / "clear.json", clear)
            if response.status != 200:
                raise RuntimeError("清空 output 失败：" + str(clear))
            if list(output.iterdir()):
                raise RuntimeError("网页清空返回成功，但 output 并非空目录")
            row.update(cleared=True, removed=clear["removed"])
            await page.locator("#test-gpu-button").click()
            await page.locator(f'input[name="test-gpu"][value="{gpu}"]').check()
            await page.locator("#test-gpu-save").click()
            async with page.expect_response(lambda r: r.url.endswith("/api/tests/plan") and r.request.method == "POST") as response_info:
                await page.locator("#test-command").click()
            response = await response_info.value
            plan = await response.json()
            save_json(evidence / "plan.json", plan)
            if response.status != 200:
                raise RuntimeError("运行命令校验失败：" + str(plan))
            await page.locator("#test-command-dialog").wait_for(state="visible")
            command = await page.locator("#test-command-text").inner_text()
            (evidence / "commands.txt").write_text(command, encoding="utf-8")
            row["cached"] = not any(line.startswith("docker load ") for line in command.splitlines())
            await page.locator("#test-command-dialog .modal-footer button").click()
            async with page.expect_response(lambda r: r.url.endswith("/api/tests/start") and r.request.method == "POST") as response_info:
                await page.locator("#test-start").click()
            response = await response_info.value
            started = await response.json()
            if response.status != 200:
                raise RuntimeError("启动失败：" + str(started))
            job = started["job"]
            row.update(job_id=job["id"], status="running", job_status=job["status"])
            save_json(evidence / "job.json", job)
            self.persist()
            offset = 0
            timed_out = False
            with (evidence / "terminal.log").open("wb") as log:
                while True:
                    data = await self.api("output?" + urlencode({"id": job["id"], "offset": offset}))
                    chunk = base64.b64decode(data["data"])
                    if data["reset"]:
                        log.write(b"\n[server buffer reset: earlier output unavailable]\n")
                    log.write(chunk); log.flush()
                    offset = data["offset"]
                    job = data["job"]
                    row["job_status"] = job["status"]
                    save_json(evidence / "job.json", job)
                    if job["status"] not in ACTIVE and not chunk:
                        break
                    if not timed_out and time.monotonic() - start_time > self.args.timeout:
                        timed_out = True
                        await page.locator("#test-stop").click()
                    if timed_out and time.monotonic() - start_time > self.args.timeout + 120:
                        raise RuntimeError("任务超时且网页停止未完成，请人工检查此任务")
                    await asyncio.sleep(1 if chunk else 2)
            row.update(exit_code=job["exit_code"], job_status=job["status"])
            after = await asyncio.to_thread(inventory, output)
            save_json(evidence / "output_inventory.json", after)
            row.update(output_files=len(after["files"]), output_bytes=sum(f["size"] for f in after["files"]),
                       nonempty_files=sum(f["size"] > 0 for f in after["files"]))
            row["new_output"] = row["nonempty_files"] > 0
            text = (evidence / "terminal.log").read_text(encoding="utf-8", errors="replace")
            # Keep full log in evidence, put only useful failure lines in the CSV.
            errors = [line.strip() for line in re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text).splitlines()
                      if re.search(r"error|exception|traceback|failed|out of memory|错误", line, re.I)]
            row["error"] = job.get("error") or " | ".join(errors[-4:])[:2000] if job["status"] != "succeeded" else ""
            row["status"] = "timeout" if timed_out else "failed" if job["status"] != "succeeded" else "no_output" if not row["new_output"] else "passed"
            if after["errors"]:
                row["error"] += " | 输出清单读取错误：" + str(after["errors"])
                row["status"] = "output_check_failed"
            try:
                await self.browse_output(page, row, after)
            except Exception as exc:
                row["preview"] = "网页检查失败：" + str(exc)[:800]
                if row["status"] == "passed":
                    row["status"] = "web_check_failed"
            await page.screenshot(path=str(evidence / "finished.png"), full_page=True)
        except Exception as exc:
            row.update(status="test_error", error=str(exc)[:2000])
            # Never implicitly kill a job after a browser/network failure; retain ID.
            if job:
                save_json(evidence / "last_known_job.json", job)
            try:
                await page.screenshot(path=str(evidence / "error.png"), full_page=True, timeout=5000)
            except Exception:
                pass
        finally:
            row.update(finished=now(), seconds=round(time.monotonic() - start_time, 2))
            save_json(evidence / "browser.json", {"dialogs": dialogs, "page_errors": page_errors})
            save_json(evidence / "result.json", row)
            await page.close()
            self.running.discard(number)
            self.persist()
            print(now(), "END", row["algorithm"], row["status"], "exit", row.get("exit_code"), "files", row.get("output_files"), row.get("error", "")[:300], flush=True)

    async def run(self):
        eligible = []
        for number in self.args.algorithms:
            name = f"algo1-4-j-{number}"
            folder = self.root / name
            archives = sorted(p.name for p in folder.iterdir() if p.is_file() and not p.is_symlink() and p.name.lower().endswith(ARCHIVES)) if folder.is_dir() and not folder.is_symlink() else []
            preferred = name + ".tar"
            self.rows[number] = {"number": number, "algorithm": name, "archive": preferred if preferred in archives else archives[0] if archives else "",
                                 "status": "pending" if archives else "skipped_no_docker", "notes": self.args.note if archives else "没有 Docker 镜像包，未清空 output"}
            if archives:
                eligible.append(number)
        self.persist()
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
            self.context = await browser.new_context(viewport={"width": 1560, "height": 1000})
            page = await self.context.new_page()
            # Wait for bootstrap's unauthenticated session check: it clears the
            # password field when switching to login and must finish before fill.
            await page.goto(self.args.url, wait_until="networkidle")
            await page.locator("#username").fill(os.environ.get("ALGO_TEST_USER", "bupt"))
            await page.locator("#password").fill(os.environ.get("ALGO_TEST_PASSWORD", "jsjskt1"))
            await page.locator("#login-button").click()
            await page.locator("#app-view").wait_for(state="visible")
            print(now(), "PREFLIGHT logged in through browser", flush=True)
            live = await self.api("jobs")
            if live["max_jobs"] != 8:
                raise RuntimeError("服务器最大任务数尚未调整为 8")
            if live["active"] and not self.args.allow_active_tests:
                raise RuntimeError("存在运行中任务，尚未进行任何 output 清空；请先等待它们结束")
            info = await self.api("info?path=algo1-4-j-74")
            if {g["index"] for g in info["gpus"]} != {"0", "1"}:
                raise RuntimeError("GPU 配置不是已确认的 0/1 两张卡")
            save_json(self.artifact / "environment.json", {"started": now(), "url": self.args.url, "root": str(self.root), "gpus": info["gpus"], "max_jobs": live["max_jobs"], "eligible": eligible, "timeout_seconds": self.args.timeout})
            await page.close()
            self.stage = "parallel"
            async def monitor():
                while True:
                    try:
                        live = await self.api("jobs")
                        self.peak_server_active = max(self.peak_server_active, live["active"])
                        self.persist()
                        print(now(), "PROGRESS", dict(Counter(r["status"] for r in self.rows.values())), "active", live["active"], flush=True)
                    except Exception as exc:
                        print(now(), "MONITOR", str(exc), flush=True)
                    await asyncio.sleep(15)
            monitor_task = asyncio.create_task(monitor())
            queues = [asyncio.Queue(), asyncio.Queue()]
            for number in eligible:
                if number != 74:
                    queues[(number - 1) % 2].put_nowait(number)
            async def worker(gpu):
                queue = queues[gpu]
                while not queue.empty():
                    number = queue.get_nowait()
                    try:
                        await self.run_one(number, str(gpu) if self.args.gpu == "alternate" else self.args.gpu)
                    finally:
                        queue.task_done()
            if self.args.workers == 1:
                await worker(0)
                await worker(1)
            else:
                await asyncio.gather(*(worker(i % 2) for i in range(self.args.workers)))
            if 74 in eligible:
                self.stage = "dual_gpu_74"
                live = await self.api("jobs")
                if live["active"]:
                    self.rows[74].update(status="not_run_active_jobs", notes="其他任务仍在运行，未清空或启动双卡算法")
                else:
                    await self.run_one(74, "all")
            self.stage = "finished"
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)
            self.persist()
            await browser.close()
        print("SUMMARY", json.dumps(dict(Counter(r["status"] for r in self.rows.values())), ensure_ascii=False), flush=True)
        print("CSV", self.artifact / "results.csv", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--root", type=Path, default=PROJECT.parent / "unzips")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=8)
    parser.add_argument("--gpu", choices=["alternate", "0", "1", "all"], default="alternate", help="Explicit override for separately authorized GPU comparison tests; all requires --workers 1")
    parser.add_argument("--allow-active-tests", action="store_true", help="Allow separate, non-overlapping retests alongside existing jobs; wait for free server/GPU capacity")
    parser.add_argument("--min-free-gpu-mib", type=int, default=0)
    parser.add_argument("--note", default="")
    parser.add_argument("--algorithms", default=",".join(str(n) for n in range(1, 132)), help="Comma-separated algorithm numbers, default 1..131; useful for separate evidence-preserving retests")
    parser.add_argument("--confirm-clear-output", action="store_true", help="REQUIRED: permanently delete existing output through the web UI")
    args = parser.parse_args()
    if not args.confirm_clear_output:
        parser.error("此测试会永久清空已有 output；必须显式指定 --confirm-clear-output")
    try:
        args.algorithms = sorted({int(n) for n in args.algorithms.split(",")})
        if not args.algorithms or any(n < 1 or n > 131 for n in args.algorithms):
            raise ValueError()
    except ValueError:
        parser.error("--algorithms 必须为 1 至 131 之间的编号，用英文逗号分隔")
    if args.gpu == "all" and args.workers != 1:
        parser.error("双卡对照测试必须 --workers 1，避免本批任务之间争抢显存")
    asyncio.run(Batch(args).run())


if __name__ == "__main__":
    main()
