#!/usr/bin/env bash
set -euo pipefail
cd /app
if [ -f /app/networkx/algorithms/isomorphism/vf2pp.py ]; then
  exit 0
fi
patch -p1 < /solution/patch.diff
