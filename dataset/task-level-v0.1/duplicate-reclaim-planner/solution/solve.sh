#!/usr/bin/env bash
set -euo pipefail
cd /app
# Idempotent: if the reclaim package is already in place, do nothing.
if [ -f /app/core/reclaim/session.py ] \
   && [ -f /app/core/reclaim/__init__.py ]; then
    exit 0
fi
git apply --whitespace=nowarn /solution/patch.diff
