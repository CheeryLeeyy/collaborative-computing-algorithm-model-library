import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/summarize_web_tests.py"


class WebSummaryTest(unittest.TestCase):
    def batch(self, root, name, rows, stage="finished"):
        path = root / name
        path.mkdir()
        (path / "results.json").write_text(json.dumps(rows), encoding="utf-8")
        (path / "progress.json").write_text(json.dumps({"stage": stage}), encoding="utf-8")
        return path

    def test_summary_preserves_failed_first_attempt_and_131_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = [{"number": n, "algorithm": f"algo1-4-j-{n}", "status": "passed", "exit_code": 0} for n in range(1, 132)]
            rows[11].update(status="failed", error="CUDA out of memory", exit_code=1)
            rows[64].update(status="failed", job_status="stopped", exit_code=143)
            evidence = root / "evidence-81"
            evidence.mkdir()
            (evidence / "plan.json").write_text(json.dumps({"warnings": ["小规模测试，不代表完整训练"]}), encoding="utf-8")
            rows[80]["evidence"] = str(evidence)
            rows[130].update(status="skipped_no_docker", exit_code="")
            first = self.batch(root, "first", rows)
            retry = self.batch(root, "retry", [{"number": 12, "algorithm": "algo1-4-j-12", "status": "passed", "exit_code": 0}])
            destination = root / "summary"
            subprocess.run([sys.executable, str(SCRIPT), str(first), str(retry), "--output", str(destination)], check=True, capture_output=True)
            with (destination / "summary.csv").open(encoding="utf-8-sig", newline="") as stream:
                result = list(csv.DictReader(stream))
            self.assertEqual(len(result), 131)
            self.assertEqual(result[11]["最终结论"], "通过")
            self.assertEqual(result[11]["首次结论"], "运行失败")
            self.assertEqual(result[11]["测试次数"], "2")
            self.assertIn("CUDA out of memory", result[11]["历次结果"])
            self.assertEqual(result[130]["测试次数"], "0")
            self.assertEqual(result[64]["最终结论"], "手动停止（待复测）")
            self.assertIn("小规模测试", result[80]["备注"])
            self.assertEqual(result[130]["最终结论"], "跳过：无Docker镜像")
            self.assertTrue((destination / "summary.csv").read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_incomplete_batch_is_not_published_as_final_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pending = self.batch(root, "pending", [], stage="parallel")
            destination = root / "summary"
            result = subprocess.run([sys.executable, str(SCRIPT), str(pending), "--output", str(destination)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(destination.exists())
