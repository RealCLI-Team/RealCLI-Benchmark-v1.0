#!/usr/bin/env bash
# Harbor verifier entrypoint: OFFICIAL WorldCoder Node scorer.
# Harbor verifier entrypoint: OFFICIAL WorldCoder Node scorer.
# reward.txt = round(tCov/100, 4) via aggregate.shape_verdict (official contract).
export PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
set -uo pipefail
WORKSPACE="/task_0000"
INSTANCE_ID="task_0000"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCORER="$SCRIPT_DIR/scorer"
mkdir -p /logs/verifier
node "$SCORER/run_eval.mjs" \
    --html-path "$WORKSPACE/index.html" \
    --rubric-path "$SCRIPT_DIR/rubric.json" \
    --assets-dir "$SCRIPT_DIR/assets" \
    --output /logs/verifier/result.json \
    2>&1 | tee /logs/verifier/test-stdout.txt
python3 - "$INSTANCE_ID" "$SCORER" <<'PY'
import json, sys
iid, scorer = sys.argv[1], sys.argv[2]
sys.path.insert(0, scorer)
from aggregate import shape_verdict, write_reward
try:
    raw = json.load(open("/logs/verifier/result.json"))
except Exception:
    raw = {"mode": "NO_ARTIFACT", "tCov": 0}
verdict = shape_verdict(iid, raw, None)
score = verdict.get("score")
write_reward("/logs/verifier", score if score is not None else 0.0)
json.dump(verdict, open("/logs/verifier/checklist_verdict.json", "w"), indent=2)
# Surface the coverage breakdown as a first-class artifact. reward.txt stays a
# single scalar (Harbor's Mean.compute rejects multi-key reward dicts), so tCov
# and vCov ride here + in the stdout line below, not in Harbor's reward channel.
reward = float(open("/logs/verifier/reward.txt").read().strip())
metrics = {k: verdict.get(k) for k in ("mode", "score", "aCov", "sCov", "tCov", "vCov")}
metrics["reward"] = reward
json.dump(metrics, open("/logs/verifier/metrics.json", "w"), indent=2)
print(f"METRICS reward={reward} tCov={metrics.get('tCov')} "
      f"vCov={metrics.get('vCov')} aCov={metrics.get('aCov')} "
      f"sCov={metrics.get('sCov')} mode={metrics.get('mode')}")
PY
# reward.txt must always be a number for Harbor; default 0 if scorer produced none.
if [ ! -s /logs/verifier/reward.txt ]; then echo 0 > /logs/verifier/reward.txt; fi
exit 0
