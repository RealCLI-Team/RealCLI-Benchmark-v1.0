#!/usr/bin/env bash
set -euo pipefail

INPUT_DIR="${1:-/app/inputs}"
OUTPUT="${2:-/app/result.json}"

python3 - "$INPUT_DIR" "$OUTPUT" <<'PY'
import json, os, sys
from collections import defaultdict

input_dir, output_path = sys.argv[1], sys.argv[2]

with open(os.path.join(input_dir, "scoring_config.json")) as f:
    cfg = json.load(f)

METRICS = ["wns", "area", "traj"]
decimals = cfg["decimals"]

def load_records():
    recs = []
    for stage_dir in ("draft", "final"):
        d = os.path.join(input_dir, stage_dir)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(d, name)) as f:
                recs.append(json.load(f))
    return recs

records = load_records()

# group by effective stage (the stage FIELD, not the folder)
by_stage = defaultdict(list)
for r in records:
    by_stage[r["stage"]].append(r)

rejected_ids = set()
survivors = []  # (stage, record)
for stage, recs in by_stage.items():
    counts = defaultdict(int)
    for r in recs:
        counts[r["run_id"]] += 1
    for r in recs:
        if counts[r["run_id"]] > 1:
            rejected_ids.add(r["run_id"])
        else:
            survivors.append((stage, r))

# precedence: final replaces draft for the same run_id
chosen = {}
for stage, r in survivors:
    rid = r["run_id"]
    if rid in rejected_ids:
        continue
    if rid not in chosen:
        chosen[rid] = (stage, r)
    else:
        prev_stage, _ = chosen[rid]
        if stage == "final" and prev_stage != "final":
            chosen[rid] = (stage, r)

def to_canonical_area(metric):
    v = metric["value"]
    unit = metric.get("unit")
    if unit == "mm2":
        return v * 1e6
    return v  # um2 or already canonical

def score_component(m, value_canonical):
    return cfg["direction"][m] * (value_canonical - cfg["baseline"][m]) / cfg["scale"][m]

scored = []
incomplete = []

for rid, (stage, r) in chosen.items():
    metrics = r.get("metrics", {})
    missing = []
    for m in METRICS:
        md = metrics.get(m)
        if md is None or md.get("value") is None:
            missing.append(m)
    if missing:
        incomplete.append({"run_id": rid, "status": "incomplete",
                           "missing": sorted(missing)})
        continue
    comps = {}
    for m in METRICS:
        md = metrics[m]
        val = to_canonical_area(md) if m == "area" else md["value"]
        comps[m] = round(score_component(m, val), decimals)
    composite = round(sum(cfg["weights"][m] * score_component(
        m, to_canonical_area(metrics[m]) if m == "area" else metrics[m]["value"])
        for m in METRICS), decimals)
    scored.append({"run_id": rid, "status": "scored", "composite": composite,
                   "components": comps})

scored.sort(key=lambda x: (-x["composite"], x["run_id"]))
incomplete.sort(key=lambda x: x["run_id"])
rejected = [{"run_id": rid, "reason": "duplicate_in_stage"} for rid in sorted(rejected_ids)]

best = scored[0]["run_id"] if scored else None

result = {
    "scored_runs": scored,
    "incomplete_runs": incomplete,
    "rejected": rejected,
    "summary": {
        "scored_count": len(scored),
        "incomplete_count": len(incomplete),
        "rejected_count": len(rejected),
        "best_run_id": best,
    },
}

with open(output_path, "w") as f:
    json.dump(result, f, indent=2, ensure_ascii=False)
    f.write("\n")

print("wrote", output_path)
PY
