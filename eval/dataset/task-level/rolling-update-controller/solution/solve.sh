#!/usr/bin/env bash
set -euo pipefail
cd /app
# Idempotent: if the rollout package is already in place, do nothing.
if [ -f /app/docker/rollout/controller.py ] \
   && [ -f /app/docker/rollout/__init__.py ]; then
    exit 0
fi
git apply --whitespace=nowarn /solution/patch.diff
