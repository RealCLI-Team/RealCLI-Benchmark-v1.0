#!/usr/bin/env bash
# Ops-side monitor helper.  Reads a cached scrape summary at
# /app/.coq_status_cache.json and prints STATUS=... for the dashboard.
# The cache is refreshed by a background worker; if the worker has not run in
# a while, this script will happily print whatever is in the cache.
set -uo pipefail

CACHE=/app/.coq_status_cache.json
if [ ! -f "$CACHE" ]; then
  echo "STATUS=UNKNOWN (cache missing)"
  exit 0
fi

status=$(python3 -c "import json; print(json.load(open('$CACHE')).get('status','UNKNOWN'))" 2>/dev/null || echo UNKNOWN)
last=$(python3 -c "import json; print(json.load(open('$CACHE')).get('last_scraped','?'))" 2>/dev/null || echo '?')
worker=$(python3 -c "import json; print(json.load(open('$CACHE')).get('refreshed_by','?'))" 2>/dev/null || echo '?')
echo "STATUS=${status}"
echo "last_scraped=${last}"
echo "refreshed_by=${worker}"
