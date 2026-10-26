#!/usr/bin/env bash
set -euo pipefail

mkdir -p /root/.interaction

# Baseline sha256 of fs/__init__.py (used by hc_top_level_fs_init_pristine).
if [ -f /app/fs/__init__.py ]; then
    sha256sum /app/fs/__init__.py > /root/.interaction/baseline_fs_init.sha256 || true
fi

# Baseline listing of fs/ directory (used by hc_top_level_fs_init_pristine).
if [ -d /app/fs ]; then
    (cd /app && find fs -type f | sort) > /root/.interaction/baseline_fs_listing.txt || true
fi

echo "post_install: baseline files written to /root/.interaction/"
