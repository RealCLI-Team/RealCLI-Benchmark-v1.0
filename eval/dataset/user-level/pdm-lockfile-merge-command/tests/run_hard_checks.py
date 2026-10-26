#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone

MAX_CAPTURE = 20000


def main() -> None:
    checks_path, out_path = sys.argv[1], sys.argv[2]
    spec = json.loads(open(checks_path, encoding="utf-8").read())
    results = []
    for check in spec.get("checks", []):
        proc = subprocess.run(
            ["bash", "-c", check["cmd"]],
            capture_output=True,
            text=True,
            timeout=120,
        )
        results.append(
            {
                "dimension": check.get("dimension", ""),
                "id": check.get("id", ""),
                "cmd": check["cmd"],
                "expect": check.get("expect", ""),
                "allow_nonzero": bool(check.get("allow_nonzero", False)),
                "exit_code": proc.returncode,
                "stdout": proc.stdout[:MAX_CAPTURE],
                "stderr": proc.stderr[:MAX_CAPTURE],
            }
        )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "instance_id": spec.get("instance_id", ""),
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "results": results,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )


if __name__ == "__main__":
    main()
