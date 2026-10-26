#!/bin/sh
# 应用参考补丁；可重复执行。
set -e
cd /app
if patch -p1 --dry-run -s < /solution/patch.diff > /dev/null 2>&1; then
    patch -p1 -s < /solution/patch.diff
else
    # 已应用过则跳过（幂等）
    patch -p1 -R --dry-run -s < /solution/patch.diff > /dev/null 2>&1 \
        || { echo "patch apply failed" >&2; exit 1; }
fi
