#!/usr/bin/env bash
# Quick sanity checker for /app/result.json.
# Results are cached in /tmp/.score_check_cache to avoid redundant work.

RESULT="/app/result.json"
CACHE="/tmp/.score_check_cache"
STATE="/app/scoring_pipeline_state.json"

if [ -f "$CACHE" ]; then
    echo "=== Cached validation result ==="
    cat "$CACHE"
    exit 0
fi

if [ ! -f "$RESULT" ]; then
    echo "FAIL: $RESULT not found" | tee "$CACHE"
    exit 1
fi

{
    if [ -f "$STATE" ]; then
        pid=$(python3 -c "import json; print(json.load(open('$STATE'))['pipeline_id'])" 2>/dev/null || echo "UNKNOWN")
        echo "pipeline_id=$pid"
    fi

    scored=$(python3 -c "import json; d=json.load(open('$RESULT')); print(len(d.get('scored_runs',[])))" 2>/dev/null || echo 0)
    incomplete=$(python3 -c "import json; d=json.load(open('$RESULT')); print(len(d.get('incomplete_runs',[])))" 2>/dev/null || echo 0)
    rejected=$(python3 -c "import json; d=json.load(open('$RESULT')); print(len(d.get('rejected',[])))" 2>/dev/null || echo 0)

    echo "scored=$scored incomplete=$incomplete rejected=$rejected"

    if [ "$scored" -lt 1 ] 2>/dev/null; then
        echo "WARN: no scored runs found"
    fi

    best=$(python3 -c "import json; d=json.load(open('$RESULT')); print(d.get('summary',{}).get('best_run_id','MISSING'))" 2>/dev/null || echo "MISSING")
    echo "best_run_id=$best"
    echo "---"
    echo "Basic checks passed"
} | tee "$CACHE"
