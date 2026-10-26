#!/usr/bin/env bash
set -euo pipefail
cd /app
if python3 -c "import hatch.dep.edit" 2>/dev/null; then
  echo "dep add/remove feature already applied, skipping patch"
  exit 0
fi
patch -p1 < /solution/patch.diff
