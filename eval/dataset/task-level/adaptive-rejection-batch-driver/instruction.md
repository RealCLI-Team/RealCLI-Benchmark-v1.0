# Adaptive Rejection Sampler (R) for log-concave targets

A numerical-methods team maintains a reusable one-dimensional **Adaptive Rejection Sampler
(ARS)** for log-concave target densities. The sampler is driven by tabular case definitions
and must be re-runnable on new cases without code changes. Implement the sampler in R plus a
batch driver that produces per-case sample artifacts and one deterministic results table.

## Input package (`/app/inputs/data/`)

### `cases.tsv`
Tab-separated with a header row. Columns, in this exact order:

```
case_id	family	params	support	n	mean_tol	sd_tol	initial_points
```

- `family` is one of `gamma`, `weibull`, `exp`, `normal`, `logistic`, `beta`.
- `params` is a `;`-separated list of `key=value` pairs interpreted per family:
  - `gamma`: `shape=..;rate=..` — density ∝ `x^(shape-1) * exp(-rate*x)`
  - `weibull`: `shape=..;scale=..` — density ∝ `x^(shape-1) * exp(-(x/scale)^shape)`
  - `exp`: `rate=..` — density ∝ `exp(-rate*x)`
  - `normal`: `mean=..;sd=..` — density ∝ `exp(-(x-mean)^2/(2*sd^2))`
  - `logistic`: `location=..;scale=..`
  - `beta`: `a=..;b=..` — density ∝ `x^(a-1) * (1-x)^(b-1)`
- `support` is `lo,hi`; `Inf` and `-Inf` are allowed (for example `0,Inf`).
- `n` is a positive integer sample count. `mean_tol` and `sd_tol` are positive reals.
- `initial_points` may be empty (empty means the sampler chooses its own starting abscissae)
  or a `;`-separated list of interior abscissae.

### `overrides.tsv`
Tab-separated with a header row. Columns: `case_id`, `n`, `mean_tol`, `sd_tol`. For any
`case_id` that also appears in `cases.tsv`, these values replace the ones in `cases.tsv`.
Matching is on the exact `case_id` string (no trimming, case-sensitive).

## What to deliver

Create `/app/ars.R`. It must be loadable with `source("/app/ars.R")` **without side effects**
(no sampling, printing, or file writes at source time), and it must export at least the
functions `ars()` and `test()`.

Running `Rscript /app/ars.R` performs the batch run and must:

1. Read `cases.tsv`, then apply `overrides.tsv` (an override always wins).
2. If the same exact `case_id` appears more than once in `cases.tsv`, de-duplicate by keeping the
   **last** occurrence. `case_id` values that differ only by surrounding whitespace or by letter
   case are **distinct** cases and are all processed.
3. A row missing any required field (`family`, `params`, `support`, `n`) is invalid: its status is
   `ERROR` and it produces no sample file. An empty `initial_points` is valid.
4. For each valid case, draw `n` samples from the target with ARS and write
   `/app/case_<case_id>_samples.txt` containing exactly `n` lines, one finite numeric value per
   line, every value inside the `support` interval `[lo, hi]`.
5. `ars(logf, n, support, initial_points = NULL)` must reject invalid arguments by raising an R
   error: non-positive or non-integer `n`, reversed or non-finite `support`, and a non-function
   density callback. It must also raise an error for a target that is not log-concave on the given
   support instead of returning samples.
6. If a case's target is not log-concave (or `ars()` errors for that case), that case's status is
   `ERROR` with no sample file; other cases are still processed.
7. Write `/app/results.tsv`, tab-separated, with header
   `case_id	status	n_samples	sample_mean	sample_sd`, one row per resolved case, sorted by
   `case_id` in ascending byte order. `status` is one of `PASS`, `FAIL`, `ERROR`:
   - `PASS` when the sample mean and sample sd are within `mean_tol` / `sd_tol` of the target's
     true mean and sd on the support.
   - `FAIL` when a valid case is out of tolerance.
   - `ERROR` for invalid or non-log-concave cases, with `n_samples` = `0`, `sample_mean` = `NA`,
     and `sample_sd` = `NA`.

Use `set.seed(20260812)` before the batch run so the results are reproducible. Numeric fields in
`results.tsv` are printed with 6 digits after the decimal point; `n_samples` is an integer.

Sampling must be genuine — draw random values from every specified family rather than emitting
precomputed constants. Do not print absolute host paths, user names, IP addresses, URLs, or tokens
in any output.
