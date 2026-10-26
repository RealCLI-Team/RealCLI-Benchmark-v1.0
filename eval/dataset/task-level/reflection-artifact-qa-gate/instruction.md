# Reflection Artifact QA Gate

You are building a quality-assurance node for a game-content pipeline. An upstream design step
produces **reflection artifacts**: each artifact summarizes one design seed with two text fields
(`unique_essence` and `meta_reflection_evidence`). Your gate must decide, per artifact, whether it
conforms to that seed's frozen design contract, and emit one machine-readable report.

## Input package

The input package lives at `/app/inputs` and contains:

- `constraints.yaml` — global thresholds:
  - `min_essence_chars` (integer)
  - `abandoned_min` (integer)
  - `abandoned_max` (integer)
- `specs/<id>.yaml` — one frozen contract per seed, each with:
  - `seed_id` (string)
  - `root_desires` — an ordered list of `snake_case` identifiers that MUST all be mentioned in the
    artifact's `unique_essence`
  - `forbidden_patterns` — a list of `{id, phrase}` entries whose `phrase` must NOT appear in the
    artifact text
- `artifacts/<id>.yaml` or `artifacts/<id>.yml` — a produced reflection artifact. Both extensions
  are equivalent and must be treated the same. It may contain:
  - `unique_essence` — a text scalar (it may be absent, present-but-empty, or present)
  - `meta_reflection_evidence` — a YAML list of abandoned-plan items (it may be absent, empty, or
    oversized)

For an artifact file `artifacts/X.yaml` or `artifacts/X.yml`, its `artifact_id` is the filename
stem `X`, and its governing spec is `specs/X.yaml` (spec files always use `.yaml`).

## Validation rules

For every artifact, evaluate the following rules and record exactly one finding for each. A finding
is an object with `rule_id`, `status` (`pass` or `fail`), and a `detail` string.

1. `essence_present` — `unique_essence` exists and is non-empty after trimming leading/trailing
   whitespace.
2. `essence_length` — the code-point length of the trimmed `unique_essence` is `>= min_essence_chars`.
3. `reflection_present` — `meta_reflection_evidence` exists and is a non-empty list.
4. `reflection_count` — the number of list items is within `[abandoned_min, abandoned_max]`,
   inclusive.
5. `root_desires_covered` — every identifier in the spec's `root_desires` appears verbatim in the
   trimmed `unique_essence` as a **case-sensitive** substring. When any are absent, `detail` lists
   the missing identifiers in spec order, formatted as `missing: <id>, <id>`.
6. `no_forbidden_patterns` — no `forbidden_patterns[].phrase` occurs as a **case-insensitive**
   substring in either `unique_essence` or the joined `meta_reflection_evidence` items. When one or
   more are hit, `detail` lists the hit `id`s in spec order, formatted as `hit: <id>, <id>`.

Behavior for edge cases:

- If an artifact has no matching spec file, emit a single finding `spec_found` with `status = fail`,
  skip all rules that require the spec, and set `overall = fail`.
- A rule whose precondition field is missing or empty fails that rule (and any dependent length,
  count, or coverage rule) rather than crashing.
- `overall` is `pass` if and only if every emitted finding for that artifact is `pass`; otherwise it
  is `fail`.

## Output

Write the report to `/app/result.json` with this shape:

```json
{"artifacts": [{"artifact_id": "...", "overall": "pass|fail",
  "findings": [{"rule_id": "...", "status": "pass|fail", "detail": "..."}]}]}
```

- Artifacts are sorted by `artifact_id` ascending.
- Within each artifact, findings are sorted by `rule_id` ascending.
- Duplicate artifact stems are not expected. If two files map to the same `artifact_id` — including
  the same stem with different extensions, e.g. `artifacts/X.yaml` and `artifacts/X.yml` — exit
  with a nonzero status and write no output file.

## How to run

Provide a program invoked as:

```
python solution.py --input /app/inputs --output /app/result.json
```
