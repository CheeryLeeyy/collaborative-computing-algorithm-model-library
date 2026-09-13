"""Interactive Docker smoke fixture; reads stdin and writes only to mounted output."""
import json
import pathlib
import subprocess
import sys

output = pathlib.Path("/app/data/output")
try:
    gpu = subprocess.run(["nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader"],
                         text=True, capture_output=True, timeout=10)
    devices = gpu.stdout.strip().splitlines()
except (OSError, subprocess.TimeoutExpired):
    devices = []
(output / "started.json").write_text(json.dumps({"gpus": devices}))
print("READY 中文交互测试", json.dumps(devices), flush=True)
for line in sys.stdin:
    value = line.strip()
    if value == "exit":
        print("COMPLETED", flush=True)
        break
    if value == "fail":
        print("EXPECTED_FAILURE", flush=True)
        sys.exit(7)
    (output / "result.txt").write_text(value)
    print("RESULT:", value, flush=True)
