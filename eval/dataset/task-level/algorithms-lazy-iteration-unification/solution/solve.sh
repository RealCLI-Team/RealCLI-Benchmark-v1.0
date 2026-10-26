#!/usr/bin/env bash
set -euo pipefail
cd /app
if git apply --check /solution/patch.diff 2>/dev/null; then
  git apply /solution/patch.diff
elif git apply --reverse --check /solution/patch.diff 2>/dev/null; then
  # patch already applied; nothing to do
  exit 0
else
  patch -p1 --forward < /solution/patch.diff
fi
