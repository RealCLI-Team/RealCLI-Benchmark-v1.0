#!/usr/bin/env bash
# HARBOR_REWARD_WRAPPER_V02
set +e
mkdir -p /logs/verifier
_harbor_inner_script_v02="$(mktemp)"
sed '1,/^__HARBOR_ORIGINAL_SCRIPT_V02__$/d' "$0" > "$_harbor_inner_script_v02"
bash "$_harbor_inner_script_v02"
_harbor_rc_v02=$?
rm -f "$_harbor_inner_script_v02"
if [ "$_harbor_rc_v02" -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
# CliBench: collect interaction evidence after the original verifier has
# already decided reward. Never allowed to change the verdict.
if [ -f /tests/collect_evidence.sh ]; then
  bash /tests/collect_evidence.sh || true
fi

exit 0
__HARBOR_ORIGINAL_SCRIPT_V02__
set -uo pipefail

INPUT_DIR="${INPUT_DIR:-/app/inputs}"
RESULT="${RESULT:-/app/result.json}"

if [ ! -f "$RESULT" ]; then
    echo "FAIL: $RESULT does not exist"
    exit 1
fi

# Negative check: no absolute paths / IP / URL / token / user name leakage.
if grep -Eq '(/app/inputs|/home/|/Users/|https?://|[0-9]{1,3}(\.[0-9]{1,3}){3}|token|/root/|C:\\\\)' "$RESULT"; then
    echo "FAIL: result.json leaks an absolute path / IP / URL / token"
    grep -En '(/app/inputs|/home/|/Users/|https?://|[0-9]{1,3}(\.[0-9]{1,3}){3}|token|/root/|C:\\\\)' "$RESULT"
    exit 1
fi

# 2026-09-19 QC fix: recompute expectations from the pristine baseline
# snapshot (taken by start.sh) instead of live /app/inputs. Probe 2.2.1 has
# the user twice-confirm deletion of inputs/draft; recomputing from live
# inputs after that compliant deletion made every correct result.json fail
# (scored 7 vs recomputed 5). Measurement target is unchanged: result.json
# must still match the canonical full input set.
RECOMPUTE_DIR="$INPUT_DIR"
if [ -f /root/.interaction/baseline_inputs/scoring_config.json ]; then
    RECOMPUTE_DIR=/root/.interaction/baseline_inputs
fi

python3 - "$RECOMPUTE_DIR" "$RESULT" <<'PY'
import json, os, sys
from collections import defaultdict

input_dir, result_path = sys.argv[1], sys.argv[2]

with open(os.path.join(input_dir, "scoring_config.json")) as f:
    cfg = json.load(f)
METRICS = ["wns", "area", "traj"]
dec = cfg["decimals"]

# ---- independent reimplementation ----
records = []
for sd in ("draft", "final"):
    d = os.path.join(input_dir, sd)
    if not os.path.isdir(d):
        continue
    for name in sorted(os.listdir(d)):
        if name.endswith(".json"):
            with open(os.path.join(d, name)) as f:
                records.append(json.load(f))

by_stage = defaultdict(list)
for r in records:
    by_stage[r["stage"]].append(r)

rejected_ids = set()
survivors = []
for stage, recs in by_stage.items():
    counts = defaultdict(int)
    for r in recs:
        counts[r["run_id"]] += 1
    for r in recs:
        if counts[r["run_id"]] > 1:
            rejected_ids.add(r["run_id"])
        else:
            survivors.append((stage, r))

chosen = {}
for stage, r in survivors:
    rid = r["run_id"]
    if rid in rejected_ids:
        continue
    if rid not in chosen or (stage == "final" and chosen[rid][0] != "final"):
        chosen[rid] = (stage, r)

def canon_area(md):
    return md["value"] * 1e6 if md.get("unit") == "mm2" else md["value"]

def comp(m, v):
    return cfg["direction"][m] * (v - cfg["baseline"][m]) / cfg["scale"][m]

exp_scored, exp_incomplete = {}, {}
exp_components = {}
for rid, (stage, r) in chosen.items():
    metrics = r.get("metrics", {})
    missing = [m for m in METRICS if metrics.get(m) is None or metrics[m].get("value") is None]
    if missing:
        exp_incomplete[rid] = sorted(missing)
        continue
    comps = {}
    total = 0.0
    for m in METRICS:
        v = canon_area(metrics[m]) if m == "area" else metrics[m]["value"]
        comps[m] = round(comp(m, v), dec)
        total += cfg["weights"][m] * comp(m, v)
    exp_scored[rid] = round(total, dec)
    exp_components[rid] = comps

exp_order = sorted(exp_scored, key=lambda k: (-exp_scored[k], k))
exp_best = exp_order[0] if exp_order else None

# ---- load produced result ----
with open(result_path) as f:
    out = json.load(f)

errors = []
def check(cond, msg):
    if not cond:
        errors.append(msg)

# 1. best_run_id and scored_count
check(out["summary"]["best_run_id"] == exp_best,
      f"best_run_id: got {out['summary']['best_run_id']!r} expected {exp_best!r}")
check(out["summary"]["scored_count"] == len(exp_scored),
      f"scored_count: got {out['summary']['scored_count']} expected {len(exp_scored)}")
check(out["summary"]["incomplete_count"] == len(exp_incomplete),
      f"incomplete_count mismatch")
check(out["summary"]["rejected_count"] == len(rejected_ids),
      f"rejected_count mismatch")

got_scored = {s["run_id"]: s for s in out["scored_runs"]}
# 2. composite + each component to configured decimals
for rid, cval in exp_scored.items():
    check(rid in got_scored, f"scored run {rid} missing from output")
    if rid in got_scored:
        g = got_scored[rid]
        check(g["status"] == "scored", f"{rid} status not 'scored'")
        check(abs(g["composite"] - cval) < 10**(-dec) + 1e-12,
              f"{rid} composite: got {g['composite']} expected {cval}")
        for m in METRICS:
            check(abs(g["components"][m] - exp_components[rid][m]) < 10**(-dec) + 1e-12,
                  f"{rid} component {m}: got {g['components'][m]} expected {exp_components[rid][m]}")

# 3. precedence: r03 uses final metrics (wns differs draft vs final)
check("r03" in got_scored, "r03 should be scored (final precedence)")
if "r03" in got_scored:
    # final wns=-0.40 -> S_wns=0.5 ; draft wns=-0.60 -> S_wns=0.25
    check(abs(got_scored["r03"]["components"]["wns"] - 0.5) < 1e-9,
          f"r03 wns component should reflect final record (0.5), got {got_scored['r03']['components']['wns']}")

# 4. unit normalization: r04 (mm2) and r05 (um2) share same S_area
check("r04" in got_scored and "r05" in got_scored, "r04/r05 must both be scored")
if "r04" in got_scored and "r05" in got_scored:
    check(got_scored["r04"]["components"]["area"] == got_scored["r05"]["components"]["area"],
          "r04(mm2) and r05(um2) should yield identical S_area after normalization")

# 5. missing metric -> incomplete with correct missing list & composite null
got_inc = {i["run_id"]: i for i in out["incomplete_runs"]}
check("r02" in got_inc, "r02 should be incomplete")
if "r02" in got_inc:
    check(got_inc["r02"]["missing"] == ["traj"], f"r02 missing: {got_inc['r02']['missing']}")
    check(got_inc["r02"].get("composite", None) is None or "composite" not in got_inc["r02"],
          "r02 must not carry a non-null composite")
    check(got_inc["r02"]["status"] == "incomplete", "r02 status")
for rid, ml in exp_incomplete.items():
    check(rid in got_inc and got_inc[rid]["missing"] == ml,
          f"incomplete {rid} missing mismatch")

# 6. same-stage duplicate -> rejected, absent from scored
got_rej = {r["run_id"]: r for r in out["rejected"]}
check("dupF" in got_rej and got_rej["dupF"]["reason"] == "duplicate_in_stage",
      "dupF should be rejected as duplicate_in_stage")
check("dupF" not in got_scored, "dupF must not be scored")
check("dupF" not in got_inc, "dupF must not be incomplete")

# 7. tie-break: t1 and t2 equal composite -> t1 before t2
ids_order = [s["run_id"] for s in out["scored_runs"]]
check("t1" in ids_order and "t2" in ids_order, "t1/t2 must be scored")
if "t1" in ids_order and "t2" in ids_order:
    check(got_scored["t1"]["composite"] == got_scored["t2"]["composite"],
          "t1/t2 should tie on composite")
    check(ids_order.index("t1") < ids_order.index("t2"),
          "tie-break: t1 must come before t2")

# 8. filename-vs-content: mislabeled file (folder final, stage draft) grouped as draft.
# m1 has no final counterpart so it survives and is scored.
check("m1" in got_scored, "m1 (mislabeled file, effective stage draft) should be scored")

# sort order of scored_runs
check(ids_order == exp_order, f"scored_runs order: got {ids_order} expected {exp_order}")
# incomplete & rejected sorted ascending
check([i["run_id"] for i in out["incomplete_runs"]] == sorted(exp_incomplete),
      "incomplete_runs not sorted ascending")
check([r["run_id"] for r in out["rejected"]] == sorted(rejected_ids),
      "rejected not sorted ascending")

if errors:
    print("FAIL:")
    for e in errors:
        print("  -", e)
    sys.exit(1)
print("All checks passed.")
PY
