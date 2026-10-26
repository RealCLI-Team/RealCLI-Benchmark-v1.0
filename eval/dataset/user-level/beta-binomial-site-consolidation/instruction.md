# Consolidate multi-center trial tables and estimate population hyperparameters

Several centers ran a small binary-endpoint pilot and exported per-site tallies
(`successes` out of `trials`). The export tooling differs across centers, some sites
re-submitted corrected numbers, and a registry file lists which sites are in scope for the
analysis. Build **one deterministic Python program** that consolidates the raw site tables,
applies the registry filter, and estimates the two population-level hyperparameters
(`alpha`, `beta`) of a hierarchical Beta-Binomial model on a fixed grid.

## Command

```
python /app/solution.py --input /app/inputs --output /app/result.json
```

- `--input` is a directory. Every file named `sites_*.csv` in it is a **site table**; the file
  `registry.csv` is the scope registry. Process site tables in **ascending filename order**
  (`sites_a.csv` before `sites_b.csv`).
- The program writes three files: `/app/result.json`, `/app/report.csv`, `/app/model.stan`.

## Reading the site tables (`/app/inputs/sites_*.csv`)

Site tables share the canonical columns `site_id`, `successes`, `trials`, but the files are
inconsistent:

- The **first line** of each site table is a header. Column names are matched
  **case-insensitively** to the canonical names `site_id`, `successes`, `trials` (so
  `Site_ID`, `Successes`, `Trials` are accepted), and the column order follows the header.
- **Delimiter detection is per line**: if a line contains a semicolon (`;`), split that line on
  `;`; otherwise split it on comma (`,`). This applies to the header line and to data lines.
- A fully **blank line** is skipped (it is not an error).
- Numeric fields may contain **thousands separators** (e.g. `1,024`) and a **trailing unit
  token** (e.g. `2,000 events`). To parse an integer from a field: remove all comma
  characters, strip surrounding whitespace, then take the leading optional-sign integer (the
  match of `-?\d+` at the start of the string); any trailing text is ignored. If there is no
  leading integer, the field is invalid.

## Canonicalization

Canonicalize every `site_id` (in the site tables **and** the registry) by stripping surrounding
whitespace and upper-casing letters. Two rows refer to the same site if and only if their
canonical ids are equal. Thus `" s01 "` and `S01` are the same site, but `S010` is a
**different** site and must not be merged with `S01`.

## Row validity and corrections

A count row is **valid** only if `successes` and `trials` both parse to integers, both are
`>= 0`, and `successes <= trials`. Any row that fails is **invalid**.

- Invalid rows contribute nothing (they do not supersede earlier values).
- If a canonical site has more than one **valid** row, that is a correction: keep the **last**
  valid row in processing order (ascending filename order, then line order within a file).

## The registry (`/app/inputs/registry.csv`)

`registry.csv` is comma-delimited with header `site_id,in_scope`. After trimming and
lower-casing, `in_scope` is either `true` or `false`. Canonicalize its `site_id` values too.

A canonical site is **kept** only if (a) it has at least one valid count row, and (b) it is
listed in the registry with `in_scope=true`. Sites that are absent from the registry, or listed
with `in_scope=false`, are excluded even if they have valid rows.

## Aggregation

Over the kept sites (each represented by its last valid `(successes, trials)`), compute:

- `n_sites`: number of kept sites,
- `total_successes`: sum of `successes`,
- `total_trials`: sum of `trials`.

A kept site whose kept `trials == 0` is legal: it is counted in `n_sites` and in the sums, but
it contributes **no** likelihood factor to the model fit and is **not** a used group.

## Model and grid computation

Let the **used groups** be the kept sites with `trials > 0`. For each used group `i` with
`k_i = successes_i`, `n_i = trials_i`:

- Observation model: `successes_i ~ Binomial(trials_i, theta_i)`.
- Group prior: `theta_i ~ Beta(alpha, beta)`.
- Hyperprior kernel: `p(alpha, beta) ∝ (alpha + beta) ^ (-5/2)` for `alpha > 0`, `beta > 0`.

Integrating out each `theta_i` gives the Beta-Binomial marginal, so the log posterior (up to an
additive constant that cancels in normalization) at a grid point `(a, b)` is:

```
logpost(a, b) = -2.5 * log(a + b)
              + sum over used groups i of [
                    lgamma(a + k_i) + lgamma(b + n_i - k_i) - lgamma(a + b + n_i)
                  - lgamma(a) - lgamma(b) + lgamma(a + b)
                ]
```

where `lgamma` is the natural log of the Gamma function.

Evaluate this on the **fixed grid** `G = {0.5, 1.0, 1.5, ..., 10.0}` (20 evenly spaced values
from `0.5` to `10.0` inclusive, step `0.5`) for both `alpha` and `beta`, i.e. all `400` ordered
pairs `(a, b)`. Using the unnormalized posterior weights `w(a, b) = exp(logpost(a, b))`, report:

```
alpha = sum(a * w(a, b)) / sum(w(a, b))
beta  = sum(b * w(a, b)) / sum(w(a, b))
```

over the whole grid. If there are **zero** used groups, the sums reduce to the hyperprior
kernel alone (still well-defined on the grid).

## Outputs

### `/app/result.json`

A JSON object with exactly these keys:

- `n_sites` (int)
- `used_groups` (int) — number of kept sites with `trials > 0`
- `total_successes` (int)
- `total_trials` (int)
- `alpha` (float, rounded to 6 decimals) — the grid posterior expectation above
- `beta` (float, rounded to 6 decimals)
- `sites`: list **sorted ascending by `site_id`**, each element
  `{"site_id": <str>, "successes": <int>, "trials": <int>}`
- `rejected`: list of the canonical `site_id` strings that appear in the site tables but are
  **not** kept (whether excluded by validity or by the registry), **unique and sorted ascending**

### `/app/report.csv`

Exact header `parameter,mean,used_groups`, followed by two rows in this order:

```
parameter,mean,used_groups
alpha,<alpha value>,<used_groups>
beta,<beta value>,<used_groups>
```

`mean` is formatted with exactly 6 digits after the decimal point. `used_groups` is the same
integer on both rows.

### `/app/model.stan`

A Stan model source (text only; it is not executed) that documents the model. It must contain:

- the observed binomial likelihood `binomial(trials, theta)`,
- the group prior statement `beta(alpha, beta)`,
- the hyperprior contribution line `target += -2.5 * log(alpha + beta);`.

## Determinism

Running the program twice on the same input must produce byte-identical output files.
