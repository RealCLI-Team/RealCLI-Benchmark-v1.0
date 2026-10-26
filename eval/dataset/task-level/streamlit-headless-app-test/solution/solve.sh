#!/usr/bin/env bash
set -euo pipefail
cd /app
if [ ! -f lib/streamlit/testing/v1/app_test.py ]; then
    patch -p1 --no-backup-if-mismatch < /solution/patch.diff
fi
