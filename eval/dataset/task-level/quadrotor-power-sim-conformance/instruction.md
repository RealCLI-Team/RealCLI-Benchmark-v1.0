# Flight-Power Simulation and Requirement Conformance Report

You are building a small offline systems-engineering tool for a quad-rotor power subsystem.

Engineers keep the subsystem parameters in a set of declarative `.model` files (a baseline file plus
revision overlays), the flight command traces in a set of scenario CSV files, and the acceptance
requirements in `requirements.json`. Your job is to write a command-line tool that loads such a
bundle, normalizes the parameters, runs a fixed-step discrete simulation for every scenario, checks
every requirement, and writes one deterministic JSON report.

## What to build

Create an executable Python program at `/app/solution.py` with this interface:

```
python3 /app/solution.py --input <bundle_dir> --output <report_path>
```

`--input` points at *any* directory that has the bundle layout described below (do not hard-code
file names, scenario names or parameter values; extra bundles with different files must work).
`--output` is the path of the JSON report to write.

A ready-made bundle ships with the environment at `/app/inputs/bundle_main`. Run your tool on it
and write the report to `/app/result.json`:

```
python3 /app/solution.py --input /app/inputs/bundle_main --output /app/result.json
```

## Bundle layout

```
<bundle_dir>/
  model/*.model          one or more parameter declaration files
  scenarios/*.csv        one or more command traces
  requirements.json      acceptance requirements
```

Only files ending in `.model` inside `model/` and files ending in `.csv` inside `scenarios/` are
considered. A scenario's `scenario_id` is its CSV file name without the `.csv` suffix.

### Parameter model files

Each meaningful line has the form

```
param <dotted.key> = <number|null> [<unit>]
```

* Blank lines and lines whose first non-blank character is `#` are ignored.
* Everything from a `#` to the end of a line is a comment and must be stripped before parsing.
* Any remaining non-blank line that does not match the grammar above is ignored.
* `null` means "declared but not assigned"; it clears any value assigned earlier for that key.
* Model files are loaded in ascending order of file name. Within a file, lines are applied top to
  bottom. A later assignment always overrides an earlier one for the same key.

Units are normalized to canonical units using exactly this table:

| unit token | canonical unit | multiply value by |
| --- | --- | --- |
| `A*h` | `A*h` | 1 |
| `mA*h` | `A*h` | 0.001 |
| `V` | `V` | 1 |
| `mV` | `V` | 0.001 |
| `W` | `W` | 1 |
| `mW` | `W` | 0.001 |
| `ms` | `ms` | 1 |
| `s` | `ms` | 1000 |
| `1` | `1` | 1 |

Any other unit token is unsupported.

These eight parameters are required:

```
battery.capacity, battery.nominal_voltage, control.arm_throttle_max, control.horizon_ms,
control.step_ms, control.timeout_ms, load.base_power, load.throttle_gain
```

Parameters outside that list may appear; they are reported but never required.

### Scenario CSV files

The first non-blank line is the header `t_ms,throttle,arm,valid` and is skipped. Blank lines are
ignored everywhere and are never counted. The remaining lines are the data rows; a data row's
*row index* is its 1-based position among the data rows in file order.

Each data row is classified as follows, in this order:

1. A row is **malformed** if it does not have exactly 4 comma-separated fields, or if any field
   cannot be parsed as a number, or if `t_ms < 0`, or if `arm` is not exactly `0` or `1`, or if
   `valid` is not exactly `0` or `1`. Malformed rows increase `malformed_rows` and are discarded.
2. Otherwise, if `throttle` is outside `[0.0, 1.0]` it is clamped into `[0.0, 1.0]` and the row
   increases `clamped_rows`. Clamping never discards a row.
3. Otherwise/afterwards, if `valid == 0` the row increases `rejected_rows` and is discarded: a
   rejected row never becomes an active command and never refreshes command age.
4. The surviving rows (`valid == 1`) are the commands, sorted ascending by `(t_ms, row index)`.

Row counting (`malformed_rows`, `clamped_rows`, `rejected_rows`) is always reported truthfully,
even for scenarios that end up in an error state.

### requirements.json

```json
{"requirements": [
  {"id": "REQ-X", "revision": 2, "metric": "soc_end", "op": ">=",
   "threshold": 0.2, "severity": "critical", "applies_to": ["*"]}
]}
```

`metric` is one of `armed_steps`, `energy_wh`, `failsafe_steps`, `mean_power_w`, `peak_throttle`,
`soc_end`. `op` is one of `>=`, `<=`, `==`. `severity` is informational only.

## Simulation

Every scenario is simulated independently; no state is inherited between scenarios.

* Let `step = control.step_ms`, `horizon = control.horizon_ms`, `N = horizon / step`.
* Step points are `t_k = k * step` for `k = 0 .. N` (that is `N + 1` step points).
* Zero-order hold: the *active command* at `t_k` is the last command in the sorted command list
  whose `t_ms <= t_k`. Its `age_ms = t_k - t_ms`. Before the first command there is no active
  command, and the age is treated as infinite.
* The controller state is one of `DISARMED`, `ARMED`, `FAILSAFE` and starts as `DISARMED`.
  At every step point the state is updated first (using the first matching rule below), and the
  resulting state is the state recorded for that step point:

  | current | rule (first match wins) | next |
  | --- | --- | --- |
  | `DISARMED` | active command exists and `arm == 1` and `throttle <= control.arm_throttle_max` | `ARMED` |
  | `DISARMED` | otherwise | `DISARMED` |
  | `ARMED` | `age_ms > control.timeout_ms` | `FAILSAFE` |
  | `ARMED` | `arm == 0` | `DISARMED` |
  | `ARMED` | otherwise | `ARMED` |
  | `FAILSAFE` | `age_ms <= control.timeout_ms` and `arm == 0` | `DISARMED` |
  | `FAILSAFE` | `age_ms <= control.timeout_ms` and `arm == 1` and `throttle <= control.arm_throttle_max` | `ARMED` |
  | `FAILSAFE` | otherwise | `FAILSAFE` |

* Effective throttle: `u_k` is the active command's (clamped) throttle when the recorded state is
  `ARMED`, and exactly `0.0` when it is `DISARMED` or `FAILSAFE`.
* Power: `P_k = load.base_power + load.throttle_gain * u_k^2` in watts.
* Energy, integrated with the averaged-endpoint rule per step, `dt = step / 1000` seconds:
  `E_J = sum over k = 0 .. N-1 of (P_k + P_{k+1}) / 2 * dt`, and `energy_wh = E_J / 3600`.
* Battery: `total_wh = battery.capacity * battery.nominal_voltage`,
  `soc_end = max(0.0, 1 - energy_wh / total_wh)`, `depleted = energy_wh > total_wh`.
* `mean_power_w = E_J / (horizon / 1000)`.
* `peak_throttle = max(u_k)` over all step points.
* `armed_steps` / `failsafe_steps` = number of step points whose recorded state is `ARMED` /
  `FAILSAFE` (step points `k = 0` and `k = N` included).
* `transitions` lists every state change in ascending step order as
  `{"t_ms": <int>, "from": <state>, "to": <state>}`, where `t_ms` is `k * step` rounded to the
  nearest integer. A change at `k = 0` is a normal transition and must be listed.

## Error codes

The only allowed `error_code` values are, verbatim:

```
"N/A", "UnsupportedUnit", "MissingParameter", "UnsetParameter",
"InvalidStepConfiguration", "EmptyScenario"
```

Conditions:

* `UnsupportedUnit` — some `param` line in some model file uses a unit token outside the table.
* `MissingParameter` — some required parameter is never declared in any model file.
* `UnsetParameter` — some required parameter is declared but its final value is `null`.
* `InvalidStepConfiguration` — `control.step_ms <= 0`, or `control.horizon_ms <= 0`, or
  `control.step_ms` is not a whole number of milliseconds, or `control.horizon_ms` is not an
  integer multiple of `control.step_ms` (integrality is checked with tolerance `1e-9`).
* `EmptyScenario` — the scenario CSV has zero data rows (header only, or no lines at all).

Precedence, first match wins:

```
UnsupportedUnit > MissingParameter > UnsetParameter > InvalidStepConfiguration > EmptyScenario
```

The first four conditions come from the model and therefore hit every scenario in the bundle.
A scenario that hits any condition gets `status = "error"`, that `error_code`, `transitions = []`,
and `null` for `armed_steps`, `failsafe_steps`, `peak_throttle`, `energy_wh`, `mean_power_w`,
`soc_end`, `depleted`. A scenario with no error gets `status = "ok"` and `error_code = "N/A"`.

Note that a scenario whose rows all parse but are all rejected (`valid == 0`) is **not** an
`EmptyScenario`: it simulates normally and simply never leaves `DISARMED`.

## Requirement evaluation

* Deduplicate by `id`: keep the entry with the largest `revision`; on equal `revision` keep the one
  that appears **later** in the `requirements` array.
* `applies_to` containing `"*"` selects all scenarios; otherwise it selects the scenarios whose
  `scenario_id` matches exactly. Names that match no scenario are ignored silently.
* Comparison uses an absolute tolerance of `1e-9`: `>=` holds when `value >= threshold - 1e-9`,
  `<=` holds when `value <= threshold + 1e-9`, `==` holds when `abs(value - threshold) <= 1e-9`.
  `value` is the reported (6-decimal) metric value.
* `verdict` is `"pass"`, `"fail"` or `"unknown"`. If the scenario `status` is not `"ok"`, or the
  requested metric is unavailable, then `verdict = "unknown"` and `observed = null`. The result's
  `error_code` is the scenario's `error_code`.

## Report format

```json
{
  "resolved_parameters": {"battery.capacity": 5.2},
  "unset_parameters": ["thermal.derate_gain"],
  "scenarios": [
    {"scenario_id": "s01_nominal", "status": "ok", "error_code": "N/A",
     "malformed_rows": 0, "clamped_rows": 0, "rejected_rows": 0,
     "armed_steps": 201, "failsafe_steps": 0, "peak_throttle": 0.6,
     "energy_wh": 0.155644, "mean_power_w": 140.07952, "soc_end": 0.998652,
     "depleted": false,
     "transitions": [{"t_ms": 0, "from": "DISARMED", "to": "ARMED"}]}
  ],
  "requirement_results": [
    {"requirement_id": "REQ-SOC", "scenario_id": "s01_nominal", "metric": "soc_end",
     "op": ">=", "threshold": 0.2, "observed": 0.998652, "verdict": "pass",
     "error_code": "N/A"}
  ],
  "summary": {"scenarios_total": 5, "scenarios_ok": 4, "requirements_evaluated": 13,
              "pass": 8, "fail": 3, "unknown": 2}
}
```

Rules:

* `resolved_parameters` holds every key whose final value is a number, expressed in canonical
  units, keys sorted ascending, values rounded to 9 decimals.
* `unset_parameters` is the ascending list of every key whose final value is `null`.
* `scenarios` is sorted ascending by `scenario_id`; each object has exactly the 14 keys shown.
* `requirement_results` is sorted ascending by `(requirement_id, scenario_id)`; each object has
  exactly the 8 keys shown. `threshold` and `observed` are numbers rounded to 6 decimals
  (or `null` for `observed`).
* `summary.requirements_evaluated` is the number of entries in `requirement_results`; `pass`,
  `fail`, `unknown` count their verdicts.
* All reported floating-point metrics are rounded to 6 decimals; `armed_steps`, `failsafe_steps`
  and the row counters are integers. Rounding happens only when writing the report — all
  intermediate arithmetic uses full double precision.
* The report must not contain any absolute filesystem path, host name or URL.

Use only the Python standard library. No network access is available.
