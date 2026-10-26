#!/usr/bin/env bash
set -euo pipefail
cd /app
# Idempotent: if the diskscan package is already in place, do nothing.
if [ -f /app/core/diskscan/session.py ] \
   && [ -f /app/core/diskscan/__init__.py ]; then
    exit 0
fi
git apply --whitespace=nowarn /solution/patch.diff
