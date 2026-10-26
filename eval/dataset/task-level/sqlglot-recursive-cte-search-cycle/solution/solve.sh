#!/usr/bin/env bash
set -euo pipefail
cd /app
# 幂等:补丁已应用过时直接退出
if grep -q "RecursiveWithCycle" /app/sqlglot/expressions/query.py; then
    exit 0
fi
patch -p1 < /solution/patch.diff
