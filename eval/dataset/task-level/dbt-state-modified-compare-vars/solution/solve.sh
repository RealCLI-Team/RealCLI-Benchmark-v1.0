#!/bin/bash
set -euo pipefail

cd /app
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git apply --check /solution/patch.diff 2>/dev/null && git apply /solution/patch.diff && exit 0
fi
# 可重复执行:已经应用过时 patch 会返回非零,此时检查反向应用来判断是否已打过补丁
if patch -p1 --dry-run -R < /solution/patch.diff > /dev/null 2>&1; then
    echo "patch already applied, skipping"
    exit 0
fi
patch -p1 < /solution/patch.diff
