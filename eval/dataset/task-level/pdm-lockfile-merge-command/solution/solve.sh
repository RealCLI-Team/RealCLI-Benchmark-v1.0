#!/usr/bin/env bash
set -euo pipefail
cd /app
if python3 -c "import pdm.project.lockfile.merge" 2>/dev/null; then
  echo "merge-lock feature already applied, skipping patch"
  exit 0
fi
patch -p1 < /solution/patch.diff
