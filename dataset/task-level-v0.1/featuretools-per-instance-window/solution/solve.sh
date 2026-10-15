#!/usr/bin/env bash
set -euo pipefail
cd /app
if patch -p1 -f --dry-run -s < /solution/patch.diff 2>/dev/null; then
  patch -p1 -f -s < /solution/patch.diff
elif patch -p1 -f -R --dry-run -s < /solution/patch.diff 2>/dev/null; then
  echo "patch already applied, skipping"
else
  echo "patch does not apply cleanly" >&2
  exit 1
fi
