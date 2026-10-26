#!/usr/bin/env bash
set -euo pipefail
cd /app
if [ ! -f gradio/event_recorder.py ]; then
    patch -p1 --no-backup-if-mismatch < /solution/patch.diff
fi
