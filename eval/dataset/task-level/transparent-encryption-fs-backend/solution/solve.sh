#!/usr/bin/env bash
set -euo pipefail
cd /app
# Idempotent: if the backend is already in place, do nothing.
if [ -f /app/fs/cryptfs.py ] && grep -q "cryptfs" /app/fs/opener/__init__.py; then
    exit 0
fi
git apply --whitespace=nowarn /solution/patch.diff
