# Independent SQL-Injection Finding Verifier

You are the automated confirmation stage of an application-security pipeline. An earlier stage
produced candidate SQL-injection findings against a small Flask-style web service. Your job is to
re-derive, **from the source code alone and using the fixed rules below**, whether each reported
finding is a genuinely reachable / exploitable injection or should be dismissed as a false positive,
and to emit a machine-readable verification report.

Treat the reported findings skeptically: the `claimed_severity` and `description` fields may be wrong.
Decide every field independently from the code.

## Input package (`/app/inputs`)

- `codebase/` — a Flask-style repository:
  - `app.py` registers HTTP routes. Each route is a decorator line `@app.route("<path>"[, methods=[...]])`
    immediately followed by `def <handler>(...):`. A handler body reads request input and may call helper
    functions by name.
  - `handlers/`, `services/` — helper modules. A helper may read request input
    (`request.args` / `request.form` / `request.values` / `request.json`) and/or run a SQL sink
    (`db.execute(...)` or `cursor.execute(...)`).
  - `safe/` — parameterized / allowlisted control implementations.
  - An access-control guard is any call named `require_auth(...)`, `require_admin(...)`, or
    `require_role(...)`.
- `findings.json` — a JSON array. Each item has: `finding_id` (str), `file` (repo-relative str),
  `line` (1-based int), `claimed_severity` (str), `description` (str). Items may be out of order and may
  contain a duplicated `finding_id`.
- `threat_model.md` — background narrative (context only; always confirm against code).

## What to produce

Write `/app/result.json`:

```
{"reviewed": <int>, "findings": [ <finding object>, ... ]}
```

For each **unique** finding, produce an object with exactly these keys:

- `finding_id` (str)
- `verdict` — `"confirmed"` or `"false_positive"`
- `reachable` — `true` / `false`
- `cvss_vector` — CVSS:3.1 base vector string, or `null`
- `sink_location` — `{"file": <str>, "line": <int>}` or `null`
- `attack_path` — string (see below)
- `false_positive_reason` — string or `null`

### Decision rules

1. **Locate the sink.** The reported `file:line` must itself contain a `db.execute(...)` or
   `cursor.execute(...)` call. If that exact line does not contain such a call, the finding is
   `false_positive`, `sink_location` is `null`, and `false_positive_reason` explains the reference
   mismatch.

2. **Injectable vs parameterized.** Determine the SQL string used by the sink call (either written
   inline in the call or assigned to the variable passed as the first argument, earlier in the same
   function). The sink is *injectable* only if that SQL string is built by concatenation (`+`),
   an f-string, `%` formatting, or `.format()` mixing in a non-literal value. A query that is a plain
   string literal passed together with bound parameters (e.g. `execute(sql, params)` using `?` / `%s`
   placeholders and no concatenation) is **not** injectable.

3. **Reachability.** The sink is *reachable* when it can be triggered from a registered route: either
   the sink function is itself a route handler that reads request input, or a route handler that reads
   request input reaches the sink function within at most 2 call hops. A helper that is never wired to
   any route is not reachable. `reachable` reflects this rule regardless of the verdict.

4. **Verdict.** A finding is `confirmed` only if it is injectable **and** reachable. Otherwise it is
   `false_positive` with a non-null `false_positive_reason` (e.g. parameterized binding, or not
   reachable from any route).

5. **CVSS.** For `confirmed`, emit exactly
   `CVSS:3.1/AV:N/AC:L/PR:<x>/UI:N/S:U/C:H/I:<x>/A:N` where:
   - `PR:L` if an access-control guard appears on the route→sink path, otherwise `PR:N`.
   - `I:H` if the SQL statement's first keyword is `INSERT`, `UPDATE`, or `DELETE`; otherwise `I:N`.
   For `false_positive`, `cvss_vector` is `null`.

6. **`false_positive_reason`.** A non-null, non-empty string exactly when the verdict is
   `false_positive`; `null` when `confirmed`.

7. **`attack_path`.** For `confirmed`, a string that contains the triggering route path and the sink
   location written as `<file>:<line>`. For `false_positive`, the empty string `""`.

8. **`sink_location`.** `{"file", "line"}` of the confirmed sink; `null` otherwise.

9. **Dedup & order.** Deduplicate by `finding_id`, keeping the first occurrence in input order.
   `reviewed` is the number of unique findings. Sort `findings` by `finding_id` ascending.

Output values must not contain absolute filesystem paths, IP addresses, URLs, or token-like strings.

## How to run

Provide a program `solution.py` invoked as:

```
python solution.py --codebase /app/inputs/codebase --findings /app/inputs/findings.json --output /app/result.json
```

Use only the Python standard library.
