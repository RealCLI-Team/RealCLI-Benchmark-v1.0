#!/usr/bin/env bash
set -euo pipefail
cd /app
# Idempotent: if the backend is already in place, do nothing.
if [ -f /app/fs/overlayfs.py ] && grep -q "overlayfs" /app/fs/opener/__init__.py; then
    exit 0
fi
# The trial plants a skeleton file at the same path the patch creates;
# remove every new-file target so git apply can create it cleanly
# (net listing unchanged — the patch recreates the same path).
git apply --whitespace=nowarn --numstat /solution/patch.diff 2>/dev/null \
  | awk '$2 == 0 {print $3}' | while IFS= read -r f; do rm -f "$f"; done
git apply --whitespace=nowarn /solution/patch.diff
