#!/usr/bin/env bash
set -euo pipefail
cd /app
# 幂等:补丁已经应用过(可以反向 dry-run)就直接成功退出。
if patch -p1 --dry-run -R < /solution/patch.diff > /dev/null 2>&1; then
  exit 0
fi
patch -p1 < /solution/patch.diff
