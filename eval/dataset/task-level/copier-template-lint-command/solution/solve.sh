#!/usr/bin/env bash
set -euo pipefail
cd /app

# Already applied? Then there is nothing to do (idempotent re-runs).
if [ -f /app/copier/_lint.py ]; then
  exit 0
fi

patch -p1 < /solution/patch.diff
