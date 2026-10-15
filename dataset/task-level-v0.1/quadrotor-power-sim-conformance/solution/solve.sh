#!/usr/bin/env bash
set -euo pipefail

# Locate the shipped bundle.
if [ -d /app/inputs/bundle_main ]; then
  BUNDLE=/app/inputs/bundle_main
elif [ -d /app/inputs/data/bundle_main ]; then
  BUNDLE=/app/inputs/data/bundle_main
else
  echo "bundle_main not found" >&2; exit 1
fi

cat > /app/solution.py <<'PYEOF'
#!/usr/bin/env python3
"""Flight-power simulation and requirement conformance report generator."""
import argparse
import json
import os
import re
import sys

PARAM_RE = re.compile(
    r"^param\s+([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*=\s*(null|[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s*\[([^\]]*)\]\s*$"
)

UNITS = {
    "A*h": ("A*h", 1.0),
    "mA*h": ("A*h", 0.001),
    "V": ("V", 1.0),
    "mV": ("V", 0.001),
    "W": ("W", 1.0),
    "mW": ("W", 0.001),
    "ms": ("ms", 1.0),
    "s": ("ms", 1000.0),
    "1": ("1", 1.0),
}

REQUIRED = [
    "battery.capacity",
    "battery.nominal_voltage",
    "control.arm_throttle_max",
    "control.horizon_ms",
    "control.step_ms",
    "control.timeout_ms",
    "load.base_power",
    "load.throttle_gain",
]

METRICS = {
    "armed_steps",
    "energy_wh",
    "failsafe_steps",
    "mean_power_w",
    "peak_throttle",
    "soc_end",
}

HEADER = ["t_ms", "throttle", "arm", "valid"]
EPS = 1e-9


def load_model(model_dir):
    values = {}
    declared = set()
    unsupported = False
    if not os.path.isdir(model_dir):
        return values, declared, unsupported
    for name in sorted(os.listdir(model_dir)):
        if not name.endswith(".model"):
            continue
        path = os.path.join(model_dir, name)
        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.split("#", 1)[0].strip()
                if not line:
                    continue
                m = PARAM_RE.match(line)
                if not m:
                    continue
                key, val, unit = m.group(1), m.group(2), m.group(3).strip()
                if unit not in UNITS:
                    unsupported = True
                    declared.add(key)
                    continue
                _canon, factor = UNITS[unit]
                declared.add(key)
                if val == "null":
                    values[key] = None
                else:
                    values[key] = float(val) * factor
    return values, declared, unsupported


def read_scenario(path):
    rows = []
    malformed = 0
    clamped = 0
    rejected = 0
    with open(path, "r", encoding="utf-8") as fh:
        lines = [ln.rstrip("\n").rstrip("\r") for ln in fh]
    data_lines = []
    seen_header = False
    for ln in lines:
        if ln.strip() == "":
            continue
        if not seen_header:
            seen_header = True
            continue
        data_lines.append(ln)
    total_rows = len(data_lines)
    for idx, ln in enumerate(data_lines, start=1):
        fields = [f.strip() for f in ln.split(",")]
        if len(fields) != 4:
            malformed += 1
            continue
        try:
            t_ms = float(fields[0])
            throttle = float(fields[1])
            arm = float(fields[2])
            valid = float(fields[3])
        except ValueError:
            malformed += 1
            continue
        if t_ms < 0:
            malformed += 1
            continue
        if arm not in (0.0, 1.0) or valid not in (0.0, 1.0):
            malformed += 1
            continue
        if throttle < 0.0 or throttle > 1.0:
            clamped += 1
            throttle = min(1.0, max(0.0, throttle))
        if valid == 0.0:
            rejected += 1
            continue
        rows.append({"t_ms": t_ms, "throttle": throttle, "arm": int(arm), "row": idx})
    rows.sort(key=lambda r: (r["t_ms"], r["row"]))
    return {
        "commands": rows,
        "malformed_rows": malformed,
        "clamped_rows": clamped,
        "rejected_rows": rejected,
        "total_rows": total_rows,
    }


def simulate(commands, params):
    step = params["control.step_ms"]
    horizon = params["control.horizon_ms"]
    timeout = params["control.timeout_ms"]
    arm_max = params["control.arm_throttle_max"]
    base = params["load.base_power"]
    gain = params["load.throttle_gain"]
    n_steps = int(round(horizon / step))

    state = "DISARMED"
    transitions = []
    powers = []
    peak = 0.0
    counts = {"DISARMED": 0, "ARMED": 0, "FAILSAFE": 0}
    ci = -1
    for k in range(n_steps + 1):
        t_k = k * step
        while ci + 1 < len(commands) and commands[ci + 1]["t_ms"] <= t_k + EPS:
            ci += 1
        cmd = commands[ci] if ci >= 0 else None
        age = (t_k - cmd["t_ms"]) if cmd is not None else None
        prev = state
        if state == "DISARMED":
            if cmd is not None and cmd["arm"] == 1 and cmd["throttle"] <= arm_max + EPS:
                state = "ARMED"
        elif state == "ARMED":
            if age is None or age > timeout + EPS:
                state = "FAILSAFE"
            elif cmd["arm"] == 0:
                state = "DISARMED"
        else:
            if age is not None and age <= timeout + EPS and cmd["arm"] == 0:
                state = "DISARMED"
            elif (
                age is not None
                and age <= timeout + EPS
                and cmd["arm"] == 1
                and cmd["throttle"] <= arm_max + EPS
            ):
                state = "ARMED"
        if state != prev:
            transitions.append(
                {"t_ms": int(round(t_k)), "from": prev, "to": state}
            )
        u = cmd["throttle"] if (state == "ARMED" and cmd is not None) else 0.0
        peak = max(peak, u)
        counts[state] += 1
        powers.append(base + gain * u * u)

    dt = step / 1000.0
    energy_j = 0.0
    for k in range(n_steps):
        energy_j += 0.5 * (powers[k] + powers[k + 1]) * dt
    energy_wh = energy_j / 3600.0
    total_wh = params["battery.capacity"] * params["battery.nominal_voltage"]
    soc = max(0.0, 1.0 - energy_wh / total_wh)
    mean_power = energy_j / (horizon / 1000.0)
    return {
        "armed_steps": counts["ARMED"],
        "failsafe_steps": counts["FAILSAFE"],
        "peak_throttle": peak,
        "energy_wh": energy_wh,
        "mean_power_w": mean_power,
        "soc_end": soc,
        "depleted": energy_wh > total_wh,
        "transitions": transitions,
    }


def dedupe_requirements(items):
    best = {}
    for item in items:
        rid = item["id"]
        rev = item.get("revision", 0)
        if rid not in best or rev >= best[rid].get("revision", 0):
            best[rid] = item
    return [best[k] for k in sorted(best)]


def compare(op, value, threshold):
    if op == ">=":
        return value >= threshold - EPS
    if op == "<=":
        return value <= threshold + EPS
    if op == "==":
        return abs(value - threshold) <= EPS
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    bundle = args.input
    values, declared, unsupported = load_model(os.path.join(bundle, "model"))

    model_error = None
    if unsupported:
        model_error = "UnsupportedUnit"
    else:
        missing = [k for k in REQUIRED if k not in declared]
        unset_required = [k for k in REQUIRED if k in declared and values.get(k) is None]
        if missing:
            model_error = "MissingParameter"
        elif unset_required:
            model_error = "UnsetParameter"
        else:
            step = values["control.step_ms"]
            horizon = values["control.horizon_ms"]
            ok = step > 0 and horizon > 0
            if ok:
                ok = abs(step - round(step)) <= EPS
            if ok:
                ratio = horizon / step
                ok = abs(ratio - round(ratio)) <= EPS and round(ratio) >= 1
            if not ok:
                model_error = "InvalidStepConfiguration"

    resolved = {k: round(v, 9) for k, v in sorted(values.items()) if v is not None}
    unset = sorted(k for k, v in values.items() if v is None)

    scen_dir = os.path.join(bundle, "scenarios")
    scen_files = sorted(f for f in os.listdir(scen_dir) if f.endswith(".csv"))
    scenarios = []
    metrics_by_scen = {}
    for fname in scen_files:
        sid = fname[: -len(".csv")]
        parsed = read_scenario(os.path.join(scen_dir, fname))
        entry = {
            "scenario_id": sid,
            "status": "ok",
            "error_code": "N/A",
            "malformed_rows": parsed["malformed_rows"],
            "clamped_rows": parsed["clamped_rows"],
            "rejected_rows": parsed["rejected_rows"],
        }
        err = model_error
        if err is None and parsed["total_rows"] == 0:
            err = "EmptyScenario"
        if err is not None:
            entry["status"] = "error"
            entry["error_code"] = err
            entry.update(
                {
                    "armed_steps": None,
                    "failsafe_steps": None,
                    "peak_throttle": None,
                    "energy_wh": None,
                    "mean_power_w": None,
                    "soc_end": None,
                    "depleted": None,
                    "transitions": [],
                }
            )
            metrics_by_scen[sid] = (None, err)
        else:
            sim = simulate(parsed["commands"], values)
            entry.update(
                {
                    "armed_steps": sim["armed_steps"],
                    "failsafe_steps": sim["failsafe_steps"],
                    "peak_throttle": round(sim["peak_throttle"], 6),
                    "energy_wh": round(sim["energy_wh"], 6),
                    "mean_power_w": round(sim["mean_power_w"], 6),
                    "soc_end": round(sim["soc_end"], 6),
                    "depleted": bool(sim["depleted"]),
                    "transitions": sim["transitions"],
                }
            )
            metrics_by_scen[sid] = (entry, "N/A")
        scenarios.append(entry)

    with open(os.path.join(bundle, "requirements.json"), "r", encoding="utf-8") as fh:
        reqdoc = json.load(fh)
    reqs = dedupe_requirements(reqdoc.get("requirements", []))

    all_ids = [s["scenario_id"] for s in scenarios]
    results = []
    for req in reqs:
        targets = req.get("applies_to", [])
        if "*" in targets:
            sids = list(all_ids)
        else:
            sids = [s for s in all_ids if s in targets]
        for sid in sorted(sids):
            entry, err = metrics_by_scen[sid]
            metric = req["metric"]
            threshold = float(req["threshold"])
            observed = None
            verdict = "unknown"
            code = err
            if entry is not None and metric in METRICS and entry.get(metric) is not None:
                observed = float(entry[metric])
                res = compare(req["op"], observed, threshold)
                if res is None:
                    verdict = "unknown"
                else:
                    verdict = "pass" if res else "fail"
            results.append(
                {
                    "requirement_id": req["id"],
                    "scenario_id": sid,
                    "metric": metric,
                    "op": req["op"],
                    "threshold": round(threshold, 6),
                    "observed": None if observed is None else round(observed, 6),
                    "verdict": verdict,
                    "error_code": code,
                }
            )
    results.sort(key=lambda r: (r["requirement_id"], r["scenario_id"]))

    report = {
        "resolved_parameters": resolved,
        "unset_parameters": unset,
        "scenarios": scenarios,
        "requirement_results": results,
        "summary": {
            "scenarios_total": len(scenarios),
            "scenarios_ok": sum(1 for s in scenarios if s["status"] == "ok"),
            "requirements_evaluated": len(results),
            "pass": sum(1 for r in results if r["verdict"] == "pass"),
            "fail": sum(1 for r in results if r["verdict"] == "fail"),
            "unknown": sum(1 for r in results if r["verdict"] == "unknown"),
        },
    }
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=False)
        fh.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
PYEOF

chmod +x /app/solution.py
python3 /app/solution.py --input "$BUNDLE" --output /app/result.json
echo "wrote /app/result.json"
