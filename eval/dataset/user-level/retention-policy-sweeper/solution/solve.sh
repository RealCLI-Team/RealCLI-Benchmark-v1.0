#!/usr/bin/env bash
set -euo pipefail
cd /app
# Idempotent: if the sweep package is already in place, do nothing.
if [ -f /app/core/sweep/session.py ] \
   && [ -f /app/core/sweep/__init__.py ]; then
    exit 0
fi
git apply --whitespace=nowarn /solution/patch.diff
