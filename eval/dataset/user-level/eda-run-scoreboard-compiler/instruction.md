# Deterministic Scoreboard Compiler

An EDA-style toolchain stores **one JSON file per run**. Runs were collected from two
sweeps: an earlier `draft/` sweep and a later `final/` sweep. Your job is to compile a single,
fully deterministic scoreboard from these run files: normalize units, apply the documented
weighted score, resolve duplicate run ids, separate runs that cannot be scored, and rank the
rest.

## Input

All inputs live under `/app/inputs`:

- `/app/inputs/scoring_config.json` — the scoring contract:

  ```json
  {
    "weights": {"wns": 0.45, "area": 0.25, "traj": 0.30},
    "direction": {"wns": 1, "area": -1, "traj": 1},
    "baseline": {"wns": -0.80, "area": 12000.0, "traj": 0.50},
    "scale":    {"wns": 0.80, "area": 12000.0, "traj": 0.50},
    "canonical_area_unit": "um2",
    "decimals": 4
  }
  ```

- `/app/inputs/draft/run_*.json` and `/app/inputs/final/run_*.json` — one record per file, e.g.:

  ```json
  {"run_id": "r03", "stage": "final",
   "metrics": {"wns": {"value": -0.40, "unit": "ns"},
               "area": {"value": 0.009, "unit": "mm2"},
               "traj": {"value": 0.62, "unit": "score"}}}
  ```

  Each record has a `run_id`, a `stage` field, and a `metrics` object with the three metrics
  `wns`, `area`, and `traj`, each carrying a `value` and a `unit`.

## Scoring rules

For each of the three metrics `m` in `{wns, area, traj}`:

```
S_m = direction[m] * (value_canonical - baseline[m]) / scale[m]
```

- `area` values must first be converted to `canonical_area_unit` (`um2`). A value given in
  `mm2` is converted with `um2 = value * 1e6`; a value already in `um2` is used as-is.
- `wns` values are in `ns` and are used as-is.
- `traj` values are unitless and are used as-is.

The composite score is:

```
composite = round( sum_m weights[m] * S_m , decimals )
```

Rounding is **round half to even** (banker's rounding) to `decimals` places.

## Stage, precedence, and deduplication

- A run's **effective stage** is taken from its `stage` **field**, not from the folder or file
  name it was found in.
- **Source precedence:** if the same `run_id` exists at both effective stages (`draft` and
  `final`), the `final` record fully replaces the `draft` record.
- **Same-stage duplicates:** if a `run_id` appears more than once **within the same effective
  stage**, none of those copies is scored. That `run_id` is placed in `rejected` with reason
  `duplicate_in_stage` — exactly **one entry per `run_id`**, not one per copy — and it does
  not appear anywhere else in the output.

## Missing metrics

If any of the three metrics is missing, or its `value` is `null`, for a kept run, that run is
`incomplete`: its `composite` is `null` and it does not participate in ranking. The `missing`
list contains the affected metric names.

## Output

Write the result to `/app/result.json` as a single JSON object with exactly this shape:

```json
{
  "scored_runs":  [{"run_id": "...", "status": "scored", "composite": 0.0,
                    "components": {"wns": 0.0, "area": 0.0, "traj": 0.0}}],
  "incomplete_runs": [{"run_id": "...", "status": "incomplete", "missing": ["area"]}],
  "rejected": [{"run_id": "...", "reason": "duplicate_in_stage"}],
  "summary": {"scored_count": 0, "incomplete_count": 0, "rejected_count": 0,
              "best_run_id": "..."}
}
```

Rules for the output:

- `scored_runs` is sorted by `composite` descending, ties broken by `run_id` ascending.
- `incomplete_runs` and `rejected` are sorted by `run_id` ascending; each `missing` list is
  sorted ascending.
- Every value in `components` is rounded to `decimals` places (round half to even).
- `summary.scored_count`, `incomplete_count`, and `rejected_count` are the lengths of the
  respective lists.
- `summary.best_run_id` is the `run_id` at the top of `scored_runs`, or `null` if no run was
  scored.
- The output must contain relative fixture references only — no absolute paths, IP addresses,
  URLs, tokens, or user names.
