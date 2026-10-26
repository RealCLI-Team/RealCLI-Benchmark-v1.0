# Security audit consolidation and release gate

A security-audit pipeline has produced several intermediate text artifacts for one
service. Your job is to consolidate them into a single machine-checkable
`/app/result.json` that contains a normalized list of confirmed findings, an
authorization-coverage metric, and a release-gate decision. Everything runs offline
against local files under `/app/inputs/data`.

## Input package (`/app/inputs/data`)

- `findings/sql_audit.md`, `findings/auth_audit.md`, `findings/file_audit.md` —
  per-category finding files. Each finding is a block that begins with a header line
  `### <id>` followed by `key: value` lines. Possible keys:
  - `category` — one of `auth`, `file`, `sql` (this field is authoritative; do not
    infer the category from the file name).
  - `severity` — optional; one of `critical`, `high`, `medium`, `low`.
  - `cvss` — optional; a number `0.0`–`10.0`.
  - `route` — the API route the finding applies to.
  - `summary` — free text.
- `revalidation.md` — the **authoritative status table**. It is a markdown table with
  columns `id | status`, where `status` is one of `Confirmed`, `Downgraded`,
  `FalsePositive`. A finding id that is not listed has the implicit status
  `Unreviewed`.
- `route_inventory.txt` — one route per line; this is the coverage **denominator**.
- `project_rules.md` — organization-wide defaults. When a rule in this instruction
  disagrees with `project_rules.md`, this instruction takes precedence.

## Required output (`/app/result.json`)

Write a JSON object with exactly these top-level keys: `findings`, `finding_count`,
`coverage`, `gate`.

### `findings`

An array. Include a finding **only if** its authoritative status in `revalidation.md`
is `Confirmed`. The status always comes from `revalidation.md`, never from a file name
or from the finding's own `severity` label. Findings that are `Downgraded`,
`FalsePositive`, or `Unreviewed` are excluded.

Each element has exactly these keys:
`{ "id", "category", "route", "severity", "cvss", "review_state" }`.

Resolve `severity`, `cvss`, and `review_state` as follows:

- If `cvss` is present, band it and set `review_state` to `"ok"`:
  - `>= 9.0` → `"critical"`
  - `>= 7.0` → `"high"`
  - `>= 4.0` → `"medium"`
  - otherwise → `"low"`
- If `cvss` is absent but a `severity` label is present, use the label and set
  `review_state` to `"ok"`.
- If both `cvss` and `severity` are absent, set `severity` to `"unknown"` and
  `review_state` to `"needs_review"`.
- `cvss` in the output is the number when present, otherwise `null`. Never invent a
  numeric value.

Deduplicate by `id`. If the same `id` appears in more than one finding block (in the
same or different files), keep exactly one element and choose the winner by:

1. highest `cvss` (a missing `cvss` counts as `-1`), then
2. category order `auth < file < sql`, then
3. lexicographically smallest `category`.

All output fields for a deduplicated id (`category`, `route`, `severity`, `cvss`,
`review_state`) come from the winning block. Two findings with **different** ids are
never merged, even if their `route` or `summary` look alike.

Sort `findings` by severity rank descending
(`critical > high > medium > low > unknown`), then by `id` ascending.

### `finding_count`

Integer equal to the length of `findings`.

### `coverage`

An object `{ "annotated": N, "total": M, "ratio": R }` where:

- `total` = number of non-empty lines in `route_inventory.txt`.
- `annotated` = number of routes in `route_inventory.txt` that appear as the `route`
  of at least one element in the output `findings` array.
- `ratio` = `annotated / total` rounded to 4 decimal places. If `total == 0`, `ratio`
  is `0.0`.

### `gate`

`"PASS"` if `coverage.ratio >= 0.80`, otherwise `"FAIL"`. This `0.80` threshold takes
precedence over any threshold stated in `project_rules.md`.

## Other requirements

- The output must not contain absolute host paths, IP addresses, URLs with a scheme
  (`http://` / `https://`), tokens, or user names.
- The result must be deterministic: running the tool twice on the same input must
  produce byte-identical `findings` ordering.

## Running

Provide a command-line tool invoked as:

```
python solution.py --input /app/inputs/data --output /app/result.json
```

It reads the input package from the `--input` directory and writes the consolidated
result to the `--output` path.
