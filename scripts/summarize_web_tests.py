"""Combine completed web test batches, preserving first-attempt/retest outcomes."""
import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

STATUS = {
    "passed": "通过", "failed": "运行失败", "no_output": "未产生非空输出",
    "web_check_failed": "网页检查失败", "output_check_failed": "输出检查失败",
    "timeout": "超时停止", "test_error": "测试流程异常",
    "skipped_no_docker": "跳过：无Docker镜像", "not_run_active_jobs": "未运行：存在其他任务",
}
COLUMNS = ["算法编号", "算法", "最终结论", "测试次数", "首次结论", "退出码", "GPU",
           "已清空output", "新输出文件数", "非空文件数", "新输出字节数", "网页目录可打开",
           "网页预览", "开始时间", "结束时间", "耗时秒", "跳过镜像加载", "任务ID",
           "错误摘要", "历次结果", "备注", "最终证据目录"]


def conclusion(row):
    if row["status"] == "failed" and row.get("job_status") == "stopped":
        return "手动停止（待复测）"
    return STATUS[row["status"]]


def runtime_notes(row):
    notes = [row.get("notes", "")]
    if row.get("evidence"):
        plan_path = Path(row["evidence"]) / "plan.json"
        if plan_path.is_file():
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            notes.extend(plan.get("warnings", []))
    return " | ".join(dict.fromkeys(n for n in notes if n))


def write_csv(path, columns, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batches", nargs="+", type=Path, help="Batch directories, oldest first")
    parser.add_argument("--output", required=True, type=Path, help="New summary directory")
    args = parser.parse_args()
    by_algorithm = defaultdict(list)
    for batch in args.batches:
        progress = json.loads((batch / "progress.json").read_text(encoding="utf-8"))
        if progress["stage"] != "finished":
            raise SystemExit(f"Batch is not finished: {batch}")
        for row in json.loads((batch / "results.json").read_text(encoding="utf-8")):
            if row["status"] not in STATUS:
                raise SystemExit(f"Incomplete result: {row['algorithm']}: {row['status']}")
            by_algorithm[row["number"]].append(row)
    if set(by_algorithm) != set(range(1, 132)):
        raise SystemExit("Combined batches must cover exactly algorithms 1..131")
    args.output.mkdir(parents=True, exist_ok=False)
    summary, attempts = [], []
    for number, history in sorted(by_algorithm.items()):
        row = history[-1]
        summary.append({
            "算法编号": number, "算法": row["algorithm"], "最终结论": conclusion(row),
            "测试次数": sum(r["status"] != "skipped_no_docker" for r in history),
            "首次结论": conclusion(history[0]), "退出码": row.get("exit_code", ""),
            "GPU": "0+1（双GPU）" if row.get("gpu") == "all" else row.get("gpu", ""),
            "已清空output": row.get("cleared", False), "新输出文件数": row.get("output_files", ""),
            "非空文件数": row.get("nonempty_files", ""), "新输出字节数": row.get("output_bytes", ""),
            "网页目录可打开": row.get("browsable", ""), "网页预览": row.get("preview", ""),
            "开始时间": row.get("started", ""), "结束时间": row.get("finished", ""),
            "耗时秒": row.get("seconds", ""), "跳过镜像加载": row.get("cached", ""),
            "任务ID": row.get("job_id", ""), "错误摘要": row.get("error", ""),
            "历次结果": " | ".join(f"{i}: {conclusion(r)} GPU={r.get('gpu', '')} exit={r.get('exit_code', '')}; {r.get('error', '')}" for i, r in enumerate(history, 1)),
            "备注": runtime_notes(row), "最终证据目录": row.get("evidence", ""),
        })
        for i, attempt in enumerate(history, 1):
            attempts.append({"算法编号": number, "算法": row["algorithm"], "次数": i,
                             "结论": conclusion(attempt), "GPU": attempt.get("gpu", ""),
                             "退出码": attempt.get("exit_code", ""), "新输出文件数": attempt.get("output_files", ""),
                             "错误摘要": attempt.get("error", ""), "证据目录": attempt.get("evidence", "")})
    write_csv(args.output / "summary.csv", COLUMNS, summary)
    write_csv(args.output / "attempts.csv", list(attempts[0]), attempts)
    counts = dict(Counter(row["最终结论"] for row in summary))
    (args.output / "summary.json").write_text(json.dumps({"counts": counts, "batches": [str(p.resolve()) for p in args.batches], "results": summary}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(counts, ensure_ascii=False))
    print(args.output / "summary.csv")


if __name__ == "__main__":
    main()
