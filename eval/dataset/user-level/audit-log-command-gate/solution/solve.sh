#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
python3 "$HERE/solve.py" \
  --policy /app/inputs/policy.json \
  --logs /app/inputs/logs \
  --out /app/result.json
