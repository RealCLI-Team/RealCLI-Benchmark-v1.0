#!/usr/bin/env bash
set -euo pipefail
cd /app
# 幂等:补丁已应用过时直接退出
if [ -f /app/scrapy/downloadermiddlewares/conditional.py ]; then
    exit 0
fi
patch -p1 < /solution/patch.diff
