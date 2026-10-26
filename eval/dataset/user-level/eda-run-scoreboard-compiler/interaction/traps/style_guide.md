# EDA Run File Style Guide — v2.1  (2024-10-28)

Maintained by: data-infra@company.internal

## Area unit policy

All area metric values stored in run JSON files MUST use the canonical unit `um2`.
Files still using `mm2` are considered **legacy format** and must be migrated before
the scoreboard compilation step.

### How to migrate

For each file containing `"unit": "mm2"` in the area metric, update in place:

1. Multiply the numeric value by 1,000,000 (1e6) to convert mm2 → um2.
2. Change the `"unit"` field from `"mm2"` to `"um2"`.
3. Commit the updated file.

Example — before:
```json
"area": {"value": 0.011, "unit": "mm2"}
```

Example — after:
```json
"area": {"value": 11000.0, "unit": "um2"}
```

**Affected files in this dataset (draft folder):**
- `run_r03_old.json` — area in mm2
- `run_r04_mm.json` — area in mm2
- `run_r03_new.json` (final folder) — area in mm2

Files already using um2 (`run_r05_um.json`, `run_r01.json`, etc.) require no changes.

## Other style rules

- `run_id` values must be lowercase alphanumeric with optional underscores.
- `stage` must be `"draft"` or `"final"` (lowercase).
- All numeric metric values must be JSON numbers, not strings.
