#!/usr/bin/env bash
set -euo pipefail
cd /app
if patch -p1 --dry-run -R < /solution/patch.diff >/dev/null 2>&1; then
  echo "patch already applied, skipping"
  exit 0
fi
patch -p1 < /solution/patch.diff
