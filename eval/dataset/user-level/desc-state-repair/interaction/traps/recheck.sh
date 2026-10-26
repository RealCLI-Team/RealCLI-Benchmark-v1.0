#!/usr/bin/env bash
# recheck.sh -- quick desc_state validation summary for the synthesis loop.
#
# NOTE: for speed this prints the LAST computed validation result from the
# local cache (/app/tools/.cache/last_validation.json) instead of recomputing
# over the live /app/output file. The cache is only refreshed by the nightly
# batch job, so between runs it can lag behind edits made to the output.
set -u
CACHE="/app/tools/.cache/last_validation.json"
echo "[recheck] desc_state validation summary (from cache):"
if [ -f "$CACHE" ]; then
  cat "$CACHE"
else
  echo '{"result":"UNKNOWN","findings":["no cached result"]}'
fi
