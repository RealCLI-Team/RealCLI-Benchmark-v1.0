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
# Content-level verification of /app/result.json and of the generality of /app/solution.py.
set -uo pipefail

FAILED=0
fail() { echo "FAIL: $*"; FAILED=1; }
pass() { echo "ok: $*"; }

APP=/app
RESULT="$APP/result.json"
SOLUTION="$APP/solution.py"

if [ -d "$APP/inputs/bundle_main" ]; then
  BUNDLE="$APP/inputs/bundle_main"
elif [ -d "$APP/inputs/data/bundle_main" ]; then
  BUNDLE="$APP/inputs/data/bundle_main"
else
  echo "FAIL: shipped bundle_main not found"; exit 1
fi

[ -f "$SOLUTION" ] || { echo "FAIL: $SOLUTION missing"; exit 1; }
[ -f "$RESULT" ]   || { echo "FAIL: $RESULT missing"; exit 1; }

# ---------------------------------------------------------------- leak check
if grep -Eq '(/root/|/home/[a-z]|/app/|[0-9]{1,3}(\.[0-9]{1,3}){3}|https?://)' "$RESULT"; then
  fail "result.json leaks an absolute path, IP or URL"
else
  pass "result.json contains no absolute path / IP / URL"
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# ------------------------------------------------- generated bundle variants
python3 - "$BUNDLE" "$WORK" <<'PYGEN'
import os, re, shutil, sys
src, work = sys.argv[1], sys.argv[2]

def clone(name):
    dst = os.path.join(work, name)
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)
    return dst

def mfiles(d):
    md = os.path.join(d, "model")
    return [os.path.join(md, f) for f in sorted(os.listdir(md)) if f.endswith(".model")]

def sub(path, key, repl):
    s = open(path, encoding="utf-8").read()
    pat = re.compile(r"^param\s+" + re.escape(key) + r"\s*=.*$", re.M)
    if pat.search(s):
        s = pat.sub(repl, s)
        open(path, "w", encoding="utf-8").write(s)
        return True
    return False

def drop(path, key):
    s = open(path, encoding="utf-8").read()
    pat = re.compile(r"^param\s+" + re.escape(key) + r"\s*=.*\n?", re.M)
    n = len(pat.findall(s))
    open(path, "w", encoding="utf-8").write(pat.sub("", s))
    return n

# v_step: horizon not an integer multiple of step
d = clone("v_step")
for f in mfiles(d):
    sub(f, "control.horizon_ms", "param control.horizon_ms = 3510 [ms]")

# v_unit: unsupported unit token
d = clone("v_unit")
for f in mfiles(d):
    sub(f, "battery.nominal_voltage", "param battery.nominal_voltage = 22.2 [kV]")

# v_missing: required parameter never declared
d = clone("v_missing")
for f in mfiles(d):
    drop(f, "load.base_power")

# v_unset: required parameter declared but null
d = clone("v_unset")
files = mfiles(d)
for f in files:
    drop(f, "load.base_power")
with open(files[-1], "a", encoding="utf-8") as fh:
    fh.write("\nparam load.base_power = null [W]\n")

# v_order: rename files so the revision overlay loads BEFORE the baseline
d = clone("v_order")
files = mfiles(d)
md = os.path.join(d, "model")
tmp = []
for i, f in enumerate(files):
    t = os.path.join(md, "tmp_%d.model" % i)
    os.rename(f, t)
    tmp.append(t)
for i, t in enumerate(tmp):
    os.rename(t, os.path.join(md, "%02d_f.model" % (90 - 10 * i)))
PYGEN
[ $? -eq 0 ] || { echo "FAIL: could not build generated bundles"; exit 1; }

for v in v_step v_unit v_missing v_unset v_order; do
  if ! python3 "$SOLUTION" --input "$WORK/$v" --output "$WORK/$v.json" >"$WORK/$v.log" 2>&1; then
    fail "solution.py failed on generated bundle $v"; sed -n '1,15p' "$WORK/$v.log"
  fi
done

# rerun on the shipped bundle to a fresh path (CLI must be reusable)
if ! python3 "$SOLUTION" --input "$BUNDLE" --output "$WORK/rerun.json" >"$WORK/rerun.log" 2>&1; then
  fail "solution.py failed on the shipped bundle"; sed -n '1,15p' "$WORK/rerun.log"
fi

# ---------------------------------------------------------------- assertions
python3 - "$RESULT" "$WORK" <<'PYCHK'
import json, math, os, sys

result_path, work = sys.argv[1], sys.argv[2]
bad = []
def chk(cond, msg):
    print(("ok: " if cond else "FAIL: ") + msg)
    if not cond:
        bad.append(msg)

def close(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol

def load(p):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)

d = load(result_path)

# ---- schema -------------------------------------------------------------
top = ["resolved_parameters", "unset_parameters", "scenarios",
       "requirement_results", "summary"]
chk(set(d) == set(top), "top level has exactly the 5 required keys")
chk(isinstance(d.get("resolved_parameters"), dict), "resolved_parameters is an object")
chk(isinstance(d.get("unset_parameters"), list), "unset_parameters is a list")
chk(isinstance(d.get("scenarios"), list), "scenarios is a list")
chk(isinstance(d.get("requirement_results"), list), "requirement_results is a list")

SCEN_KEYS = {"scenario_id", "status", "error_code", "malformed_rows", "clamped_rows",
             "rejected_rows", "armed_steps", "failsafe_steps", "peak_throttle",
             "energy_wh", "mean_power_w", "soc_end", "depleted", "transitions"}
REQ_KEYS = {"requirement_id", "scenario_id", "metric", "op", "threshold",
            "observed", "verdict", "error_code"}
CODES = {"N/A", "UnsupportedUnit", "MissingParameter", "UnsetParameter",
         "InvalidStepConfiguration", "EmptyScenario"}
STATES = {"DISARMED", "ARMED", "FAILSAFE"}

S = {s["scenario_id"]: s for s in d["scenarios"]}
for s in d["scenarios"]:
    sid = s["scenario_id"]
    chk(set(s) == SCEN_KEYS, "scenario %s has exactly the 14 required keys" % sid)
    chk(s["error_code"] in CODES, "scenario %s error_code within enum" % sid)
    chk(s["status"] in ("ok", "error"), "scenario %s status within enum" % sid)
    for tr in s["transitions"]:
        chk(set(tr) == {"t_ms", "from", "to"} and tr["from"] in STATES
            and tr["to"] in STATES and isinstance(tr["t_ms"], int),
            "scenario %s transition well formed" % sid)
for r in d["requirement_results"]:
    chk(set(r) == REQ_KEYS, "requirement result %s/%s has exactly the 8 keys"
        % (r.get("requirement_id"), r.get("scenario_id")))
    chk(r["verdict"] in ("pass", "fail", "unknown"), "verdict within enum")
    chk(r["error_code"] in CODES, "requirement error_code within enum")

# ---- parameter overlay / units -----------------------------------------
rp = d["resolved_parameters"]
chk(list(rp) == sorted(rp), "resolved_parameters keys ascending")
chk(close(rp.get("battery.capacity"), 5.2, 1e-9),
    "battery.capacity = 5.2 A*h (revision value in [mA*h] converted)")
chk(close(rp.get("battery.nominal_voltage"), 22.2, 1e-9),
    "battery.nominal_voltage = 22.2 V (converted from [mV])")
chk(close(rp.get("load.base_power"), 10.0, 1e-9),
    "load.base_power = 10.0 W (revision overrides baseline)")
chk(close(rp.get("control.horizon_ms"), 4000.0, 1e-9),
    "control.horizon_ms = 4000 ms (converted from [s])")
chk(close(rp.get("load.throttle_gain"), 480.0, 1e-9), "load.throttle_gain = 480.0 W")
chk("thermal.derate_gain" not in rp,
    "null-cleared key absent from resolved_parameters")
chk(d["unset_parameters"] == ["thermal.derate_gain"],
    "unset_parameters == ['thermal.derate_gain']")

# ---- closed-form check for the nominal scenario -------------------------
base, gain, cap, volt = 10.0, 480.0, 5.2, 22.2
step, horizon = 20.0, 4000.0
N = int(horizon / step)
p_lo = base + gain * 0.02 ** 2
p_hi = base + gain * 0.6 ** 2
powers = [p_lo] * 50 + [p_hi] * (N + 1 - 50)
dt = step / 1000.0
E = sum((powers[k] + powers[k + 1]) / 2.0 * dt for k in range(N))
exp_wh = E / 3600.0
exp_mean = E / (horizon / 1000.0)
exp_soc = max(0.0, 1.0 - exp_wh / (cap * volt))

s1 = S.get("s01_nominal", {})
chk(s1.get("status") == "ok" and s1.get("error_code") == "N/A", "s01_nominal ok")
chk(close(s1.get("energy_wh"), exp_wh), "s01_nominal energy_wh == %.6f" % exp_wh)
chk(close(s1.get("mean_power_w"), exp_mean), "s01_nominal mean_power_w == %.6f" % exp_mean)
chk(close(s1.get("soc_end"), exp_soc), "s01_nominal soc_end == %.6f" % exp_soc)
chk(close(s1.get("peak_throttle"), 0.6), "s01_nominal peak_throttle == 0.6")
chk(s1.get("armed_steps") == N + 1, "s01_nominal armed_steps == %d" % (N + 1))
chk(s1.get("failsafe_steps") == 0, "s01_nominal failsafe_steps == 0")
chk(s1.get("depleted") is False, "s01_nominal depleted is false")
chk(s1.get("transitions") == [{"t_ms": 0, "from": "DISARMED", "to": "ARMED"}],
    "s01_nominal transitions == single arm at t=0")

# ---- state machine: timeout / failsafe recovery gating ------------------
s2 = S.get("s02_timeout", {})
chk(s2.get("status") == "ok", "s02_timeout ok")
chk(s2.get("rejected_rows") == 2, "s02_timeout rejected_rows == 2")
chk(s2.get("malformed_rows") == 0, "s02_timeout malformed_rows == 0")
chk(s2.get("transitions") == [
        {"t_ms": 0, "from": "DISARMED", "to": "ARMED"},
        {"t_ms": 620, "from": "ARMED", "to": "FAILSAFE"},
        {"t_ms": 1400, "from": "FAILSAFE", "to": "ARMED"},
        {"t_ms": 2200, "from": "ARMED", "to": "DISARMED"}],
    "s02_timeout transition sequence exact")
chk(s2.get("failsafe_steps") == 39, "s02_timeout failsafe_steps == 39")
chk(s2.get("armed_steps") == 71, "s02_timeout armed_steps == 71")
chk(close(s2.get("peak_throttle"), 0.5), "s02_timeout peak_throttle == 0.5")

# ---- format drift: clamping, not dropping -------------------------------
s3 = S.get("s03_noisy", {})
chk(s3.get("status") == "ok", "s03_noisy ok")
chk(s3.get("malformed_rows") == 3, "s03_noisy malformed_rows == 3")
chk(s3.get("clamped_rows") == 2, "s03_noisy clamped_rows == 2")
chk(s3.get("rejected_rows") == 0, "s03_noisy rejected_rows == 0")
chk(close(s3.get("peak_throttle"), 1.0), "s03_noisy peak_throttle == 1.0 (clamped)")
chk(s3.get("peak_throttle") is not None and s3["peak_throttle"] <= 1.0,
    "s03_noisy peak_throttle <= 1.0")
chk(s3.get("transitions") == [
        {"t_ms": 0, "from": "DISARMED", "to": "ARMED"},
        {"t_ms": 1020, "from": "ARMED", "to": "FAILSAFE"}],
    "s03_noisy transitions exact (high throttle never re-arms)")
chk(s3.get("armed_steps") == 51, "s03_noisy armed_steps == 51")
chk(s3.get("failsafe_steps") == 150, "s03_noisy failsafe_steps == 150")

# ---- all-rejected is NOT an empty scenario ------------------------------
s4 = S.get("s04_all_rejected", {})
chk(s4.get("status") == "ok", "s04_all_rejected simulates (status ok)")
chk(s4.get("error_code") == "N/A", "s04_all_rejected error_code N/A")
chk(s4.get("rejected_rows") == 4, "s04_all_rejected rejected_rows == 4")
chk(s4.get("armed_steps") == 0, "s04_all_rejected armed_steps == 0")
chk(s4.get("transitions") == [], "s04_all_rejected has no transitions")
idle_wh = base * (horizon / 1000.0) / 3600.0
chk(close(s4.get("energy_wh"), idle_wh), "s04_all_rejected energy_wh == %.6f" % idle_wh)
chk(close(s4.get("mean_power_w"), base), "s04_all_rejected mean_power_w == 10.0")

s5 = S.get("s05_empty", {})
chk(s5.get("status") == "error", "s05_empty status error")
chk(s5.get("error_code") == "EmptyScenario", "s05_empty error_code EmptyScenario")
chk(all(s5.get(k) is None for k in ("armed_steps", "failsafe_steps", "peak_throttle",
                                    "energy_wh", "mean_power_w", "soc_end", "depleted")),
    "s05_empty metrics all null")
chk(s5.get("transitions") == [], "s05_empty transitions empty")
chk(s4.get("error_code") != s5.get("error_code"),
    "all-rejected and empty scenarios do not share an error code")

# ---- ordering / dedup ---------------------------------------------------
ids = [s["scenario_id"] for s in d["scenarios"]]
chk(ids == ["s01_nominal", "s02_timeout", "s03_noisy", "s04_all_rejected", "s05_empty"],
    "scenarios ordered by scenario_id")
pairs = [(r["requirement_id"], r["scenario_id"]) for r in d["requirement_results"]]
chk(pairs == sorted(pairs), "requirement_results ordered by (id, scenario)")
chk(len(pairs) == len(set(pairs)), "no duplicated (requirement, scenario) pair")

soc = [r for r in d["requirement_results"] if r["requirement_id"] == "REQ-SOC"]
chk(len(soc) == 5, "REQ-SOC evaluated once per scenario")
chk(all(close(r["threshold"], 0.2, 1e-9) for r in soc),
    "REQ-SOC threshold 0.2 (highest revision wins)")
peak = [r for r in d["requirement_results"] if r["requirement_id"] == "REQ-PEAK"]
chk([r["scenario_id"] for r in peak] == ["s01_nominal", "s03_noisy"],
    "REQ-PEAK applies only to its two named scenarios")
chk(all(close(r["threshold"], 0.6, 1e-9) for r in peak),
    "REQ-PEAK threshold 0.6 (later entry wins on equal revision)")
byp = {(r["requirement_id"], r["scenario_id"]): r for r in d["requirement_results"]}
chk((byp.get(("REQ-PEAK", "s01_nominal")) or {}).get("verdict") == "pass",
    "REQ-PEAK passes on s01_nominal at tolerance")
chk((byp.get(("REQ-PEAK", "s03_noisy")) or {}).get("verdict") == "fail", "REQ-PEAK fails on s03_noisy")
chk((byp.get(("REQ-FS", "s02_timeout")) or {}).get("verdict") == "fail", "REQ-FS fails on s02_timeout")
chk(close((byp.get(("REQ-FS", "s02_timeout")) or {}).get("observed"), 39.0),
    "REQ-FS observed 39 on s02_timeout")
chk((byp.get(("REQ-FS", "s05_empty")) or {}).get("verdict") == "unknown"
    and (byp.get(("REQ-FS", "s05_empty")) or {}).get("observed") is None
    and (byp.get(("REQ-FS", "s05_empty")) or {}).get("error_code") == "EmptyScenario",
    "REQ-FS unknown on s05_empty with scenario error code")
energy = [r for r in d["requirement_results"] if r["requirement_id"] == "REQ-ENERGY"]
chk([r["scenario_id"] for r in energy] == ["s01_nominal"],
    "REQ-ENERGY ignores the applies_to name matching no scenario")

# ---- summary ------------------------------------------------------------
sm = d["summary"]
chk(set(sm) == {"scenarios_total", "scenarios_ok", "requirements_evaluated",
                "pass", "fail", "unknown"}, "summary has exactly the 6 keys")
chk(sm["scenarios_total"] == 5 and sm["scenarios_ok"] == 4, "summary scenario counts")
chk(sm["requirements_evaluated"] == len(d["requirement_results"]),
    "summary requirements_evaluated matches list length")
for v in ("pass", "fail", "unknown"):
    chk(sm[v] == sum(1 for r in d["requirement_results"] if r["verdict"] == v),
        "summary %s count matches verdicts" % v)
chk(sm["requirements_evaluated"] == 13, "13 requirement results in total")

# ---- generated bundles --------------------------------------------------
v = load(os.path.join(work, "v_step.json"))
chk({s["error_code"] for s in v["scenarios"]} == {"InvalidStepConfiguration"},
    "non-divisible horizon -> InvalidStepConfiguration for every scenario")
chk(all(s["status"] == "error" for s in v["scenarios"]),
    "non-divisible horizon -> every scenario in error")
chk({r["verdict"] for r in v["requirement_results"]} == {"unknown"},
    "non-divisible horizon -> every verdict unknown")
chk(all(r["observed"] is None for r in v["requirement_results"]),
    "non-divisible horizon -> every observed null")

v = load(os.path.join(work, "v_unit.json"))
chk({s["error_code"] for s in v["scenarios"]} == {"UnsupportedUnit"},
    "unsupported unit takes precedence for every scenario")
chk("battery.nominal_voltage" not in v["resolved_parameters"],
    "unsupported unit value not resolved")

v = load(os.path.join(work, "v_missing.json"))
chk({s["error_code"] for s in v["scenarios"]} == {"MissingParameter"},
    "undeclared required parameter -> MissingParameter")

v = load(os.path.join(work, "v_unset.json"))
chk({s["error_code"] for s in v["scenarios"]} == {"UnsetParameter"},
    "required parameter declared null -> UnsetParameter")
chk("load.base_power" in v["unset_parameters"],
    "null required parameter listed in unset_parameters")

v = load(os.path.join(work, "v_order.json"))
chk(close(v["resolved_parameters"].get("load.base_power"), 12.5, 1e-9),
    "file-name order decides the override (baseline wins when it sorts last)")
chk(close(v["resolved_parameters"].get("battery.capacity"), 6.0, 1e-9),
    "baseline capacity 6.0 A*h wins when it sorts last")
chk(close(v["resolved_parameters"].get("thermal.derate_gain"), 3.0, 1e-9),
    "value re-assigned after a null is resolved again")
chk(v["unset_parameters"] == [], "no unset parameters once baseline sorts last")

rr = load(os.path.join(work, "rerun.json"))
chk(rr == d, "rerunning the tool on the shipped bundle reproduces result.json")

print("")
if bad:
    print("FAILED CHECKS: %d" % len(bad))
    sys.exit(1)
print("ALL PYTHON CHECKS PASSED")
PYCHK
[ $? -eq 0 ] || FAILED=1

if [ "$FAILED" -ne 0 ]; then
  echo "RESULT: FAIL"
  exit 1
fi
echo "RESULT: PASS"
