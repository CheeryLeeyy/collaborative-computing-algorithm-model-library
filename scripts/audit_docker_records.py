#!/usr/bin/env python3
"""Read successful historical records and emit a compact, reviewable runtime manifest.

No container is executed and no source record is modified. Generated output goes
to stdout; review it before updating verified_runs.json in this project.
"""
import argparse
import json
import re
import shlex
from pathlib import Path


def audit(root):
    selected = {}
    scanned = 0
    for pattern in ("run.json", "run_summary.json", "run_summary_compact.json"):
        for record in root.rglob(pattern):
            if "failed-attempts" in record.parts or record.is_symlink() or record.stat().st_size > 32 * 1024 * 1024:
                continue
            try:
                data = json.loads(record.read_text())
            except (ValueError, OSError):
                continue
            if not isinstance(data, dict):
                continue
            scanned += 1
            name = data.get("package", "")
            if not re.fullmatch(r"algo[0-9a-z-]+", name) or data.get("run", {}).get("exit_code") != 0:
                continue
            if data.get("output_inventory", {}).get("file_count", 0) < 1:
                continue
            command = data.get("docker_command")
            if not command:
                command = shlex.split(data.get("docker_command_shell", ""))
            image = data.get("image", name + ":v1")
            if command[:2] != ["docker", "run"] or image not in command:
                continue
            boundary = command.index(image)
            prefix = command[2:boundary]
            options, environment = {}, {}
            unsupported = []
            i = 0
            while i < len(prefix):
                option, _, inline = prefix[i].partition("=")
                i += 1
                if option in {"--rm", "-it", "-i", "-t", "--interactive", "--tty"}:
                    continue
                if not inline:
                    if i >= len(prefix):
                        unsupported.append(option); break
                    inline = prefix[i]; i += 1
                if option in {"--name", "--volume", "-v"}:
                    if option in {"--volume", "-v"} and not re.search(r":/app/data/(input|output)(?::ro)?$", inline):
                        unsupported.append("extra mount " + inline)
                elif option in {"--gpus", "--shm-size", "--platform", "--memory", "--cpus", "--pids-limit"}:
                    options[option[2:]] = inline
                elif option in {"--env", "-e"}:
                    key, _, value = inline.partition("=")
                    environment[key] = value
                else:
                    unsupported.append(option)
            item = {"record": str(record.relative_to(root)), "started_at": data.get("started_at", ""),
                    "case": data.get("case", "default"), "image": image, "options": options,
                    "environment": environment, "command": command[boundary+1:],
                    "exit_code": 0, "output_files": data["output_inventory"]["file_count"],
                    "unsupported": unsupported}
            previous = selected.get(name)
            if previous is None or item["started_at"] > previous["started_at"]:
                selected[name] = item
    return {"schema": 1, "audited_on": "2026-09-14", "records_scanned": scanned,
            "note": "Historical success is not a guarantee for modified archives or inputs. GPU and mount paths are always supplied by the current website job.",
            "algorithms": dict(sorted(selected.items(), key=lambda x: int(x[0].rsplit("-", 1)[-1])))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.root), ensure_ascii=False, indent=2))
