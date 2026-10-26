#!/usr/bin/env bash
set -euo pipefail
cd /app
# Idempotent: if the orchestration package is already in place, do nothing.
if [ -f /app/docker/orchestration/deployer.py ] \
   && [ -f /app/docker/orchestration/__init__.py ]; then
    exit 0
fi
git apply --whitespace=nowarn /solution/patch.diff
