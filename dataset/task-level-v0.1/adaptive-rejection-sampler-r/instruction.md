# Adaptive Rejection Sampler in R (TSV-driven)

You are building a small, reproducible sampling toolkit for a research group. The
team maintains a table of distribution "cases" and wants a **general-purpose
adaptive rejection sampler (ARS)** written in R that can run every case, write the
drawn samples to disk, and print a per-case status line so reviewers can confirm
the draws statistically match the target distribution.

Everything runs offline with base R only (`stats` included). No extra packages.

## What you must deliver

Create a single R source file at `/app/ars.R`. Sourcing it with
`source("ars.R")` **must have no side effects** (it must not sample, print, read
files, or error) and must define the following functions:

- `ars(g, lower, upper, n, seed = NULL, ...)`
- `read_cases(path)`
- `build_log_density(family, params)`
- `true_moments(family, params)`
- `test(cases_path, out_dir)`

### `ars(g, lower, upper, n, seed = NULL, ...)`

Draw `n` samples from an (unnormalized) **log-concave** target on the interval
`[lower, upper]`, using adaptive rejection sampling. `g` is a **log-density
callback**: it returns the natural log of the unnormalized density. It may be
supplied as a **vectorized** function (accepts a numeric vector, returns a numeric
vector) or as a **scalar-only** function (accepts one number). Bounds may be
finite or infinite (`Inf` / `-Inf`).

`ars()` must call `stop()` (raise an error) when:

- `n` is not a positive integer (e.g. `0`, negative, or non-integer),
- `lower >= upper`,
- `g` is not a function, or
- the target is detected to **not be log-concave** on the support.

It must never return canned or precomputed numbers; it must perform real sampling.
For a fixed `seed`, repeated calls must produce **identical** output.

### `build_log_density(family, params)`

Return the log-density callback for a distribution `family` with named numeric
parameters `params` (an R list). Supported families and parameters:

| family    | params (keys)         | support        | log-density (unnormalized)                     |
|-----------|-----------------------|----------------|------------------------------------------------|
| `gamma`   | `shape`, `rate`       | `[0, Inf)`     | `(shape-1)*log(x) - rate*x`                     |
| `weibull` | `shape`, `scale`      | `[0, Inf)`     | `(shape-1)*log(x) - (x/scale)^shape`            |
| `exp`     | `rate`                | `[0, Inf)`     | `-rate*x`                                       |
| `beta`    | `shape1`, `shape2`    | `(0, 1)`       | `(shape1-1)*log(x) + (shape2-1)*log(1-x)`       |

The `family` string may arrive with surrounding whitespace or mixed case (e.g.
`"  Gamma "`) and must still be recognized.

### `true_moments(family, params)`

Return a list with the analytic `mean` and `sd` of the named distribution:

- `gamma`: mean `shape/rate`, sd `sqrt(shape)/rate`
- `weibull`: mean `scale*Gamma(1+1/shape)`, variance `scale^2*(Gamma(1+2/shape) - Gamma(1+1/shape)^2)`
- `exp`: mean `1/rate`, sd `1/rate`
- `beta`: mean `shape1/(shape1+shape2)`, variance `(shape1*shape2)/((shape1+shape2)^2*(shape1+shape2+1))`

### `read_cases(path)` and the case table

The case table is a **tab-separated** file. Its header is:

```
case_id	family	params	lower	upper	n	seed	mean_tol	sd_tol
```

Column meaning:

- `case_id` — unique label used to name the output file.
- `family` — one of `gamma`, `weibull`, `exp`, `beta` (may have whitespace / mixed case).
- `params` — `;`-separated `key=value` pairs, e.g. `shape=2;rate=1.5`. Surrounding
  spaces around keys, values, and separators may occur and must be tolerated.
- `lower`, `upper` — numeric bounds (may be `Inf`).
- `n` — number of samples to draw.
- `seed` — integer seed for reproducibility.
- `mean_tol`, `sd_tol` — absolute tolerances used to grade the case.

`read_cases(path)` reads this file, **skips fully blank lines**, and returns the
parsed rows. Rows with leading/trailing whitespace in fields must be handled by
trimming.

### `test(cases_path, out_dir)`

Read the case table, create `out_dir` if needed, and process **every** non-blank
row:

- For each case that samples successfully, write
  `<out_dir>/<case_id>_samples.txt` containing **only** the numeric samples, one
  value per line, exactly `n` lines. Every value must be finite and lie within
  `[lower, upper]`.
- Print exactly one status line per case:
  - `CASE_ID: PASS mean=<m> sd=<s>` when both
    `|sample_mean - true_mean| <= mean_tol` and `|sample_sd - true_sd| <= sd_tol`;
  - otherwise `CASE_ID: FAIL mean=<m> sd=<s>`.
- A row that is invalid (bad `n`, `lower >= upper`, or a non-log-concave target)
  must **not** produce a sample file; report it as a failing/errored case, e.g.
  `CASE_ID: FAIL (error: ...)`.

## Input package

The case table is provided at `/app/inputs/data/cases.tsv`. It contains valid
cases across several families (including two `gamma` cases whose parameters are
close but different), a valid row with whitespace/mixed-case `family`, one blank
line to skip, and some invalid rows (a zero sample count, reversed bounds, and a
`gamma` shape that is not log-concave).

## Reference command

The toolkit is exercised with:

```
Rscript -e 'source("ars.R"); test("inputs/data/cases.tsv","out")'
```

which must write the per-case `*_samples.txt` files into `/app/out/` and print the
status lines described above.

**This command is a required delivery step, not just an example.** Before
finishing, you must actually run it (from `/app`) so that `/app/out/` exists and
contains one `<case_id>_samples.txt` file for every valid case in the table
(`g_gamma_a`, `g_gamma_b`, `w_weib`, `e_exp`, `g_gamma_ws`, `b_beta`), and no
sample file for any invalid row. The grader checks only these pre-existing
output files — it does not run the command for you.
