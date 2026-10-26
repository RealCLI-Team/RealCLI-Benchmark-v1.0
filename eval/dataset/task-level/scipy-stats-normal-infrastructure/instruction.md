# Land the next-generation continuous distribution infrastructure for scipy.stats (first release: the normal distribution)

## Background

All existing continuous distributions in `scipy.stats` are built on the legacy `rv_continuous` architecture: distribution behavior is expressed through class-level "frozen/unfrozen methods + `loc`/`scale` standard parameterization". This design carries plenty of historical baggage: non-standard parameterizations cannot be expressed, the complementary CDF (CCDF) and log-domain inverse CDF lose accuracy in the deep tails, invalid parameters immediately raise an exception for the whole call, and vectorization support is incomplete. After years of community discussion, we have decided to actually land the next-generation continuous distribution infrastructure in the scipy source tree at `/app`, with the normal distribution as the first publicly available distribution.

The code is at `/app`. Read through the existing repository code first (especially the style of the implementations under `scipy/stats/`, and the accurate special functions available in `scipy.special`), then decide on the location and internal design of the new code yourself. The requirements below are the external behavior contract; the internal architecture is up to you.

## Requirements

**1. An API oriented around "random variable" objects.** The new API centers on random variable objects: first instantiate an object with the distribution parameters, then call methods on the object. The normal distribution must be importable from the top level of `scipy.stats`, i.e. `scipy.stats.Normal`. At construction, the parameters `mu` (mean) and `sigma` (standard deviation) are both passed as keyword arguments, with default values 0 and 1 respectively; passing no arguments yields a standard normal random variable. After instantiation the parameters should be readable back as attributes (e.g. `X.mu`, `X.sigma`).

**2. A complete method set.** The random variable object provides at least the following public methods:

- Density and cumulative: `pdf`, `logpdf`, `cdf`, `logcdf`, plus the right-tail versions `ccdf`, `logccdf`;
- `cdf` supports a two-argument form: `X.cdf(x, y)` returns P(x ≤ X ≤ y);
- Inverse functions: `icdf`, `iccdf`, `ilogcdf`, `ilogccdf`. Here `ilogcdf`/`ilogccdf` take a log-probability as input and are meant for the deep-tail region where plain `icdf` fails due to underflow; they must still return finite and accurate results;
- Distribution characteristics: `mean`, `variance`, `standard_deviation`, `skewness`, `kurtosis`, `median`, `mode`. `kurtosis` uses the non-excess (Pearson) convention by default (i.e. the kurtosis of the normal distribution is 3), and also supports `convention='excess'` to return the excess kurtosis;
- Generic moments: `moment(order, kind)`, where `kind` is one of `'raw'`, `'central'`, `'standardized'`;
- Entropy: `entropy`, `logentropy`;
- `support()` returns the lower and upper endpoints of the support interval;
- `sample(shape, rng)` draws pseudo-random samples: `rng` accepts a `numpy.random.Generator` or a seed that can be passed to `numpy.random.default_rng`, and results are reproducible under the same seed; when the distribution parameters are arrays, the shape of the returned samples is `shape + 参数广播形状`.

**3. Vectorization and robustness.** Distribution parameters may be arrays, and all methods compute elementwise under NumPy broadcasting rules. When some parameters are invalid (e.g. `sigma <= 0`), the output at the corresponding positions is NaN rather than the whole call raising an exception — a single bad element in the array must not take down the computation of the other elements.

**4. Numerical correctness.** The numerical results of each method must agree with SciPy's existing capabilities (e.g. the old architecture's `scipy.stats.norm`, the `ndtr`/`ndtri` functions in `scipy.special`), reaching floating-point precision in the regular region; the deep-tail region stays accurate via the log-domain interfaces.

## Build and verification

The repository has already been fully compiled once with Meson in the `/app/build` directory and installed into the current Python environment. After you modify pure-Python source code, running `ninja -C /app/build install` incrementally recompiles and reinstalls it, and the changes take effect immediately. When finished, self-check: open a fresh Python interpreter in a directory outside `/app` and run

```bash
python -c "from scipy import stats; X = stats.Normal(mu=1., sigma=2.); print(X.mean(), X.variance())"
```

It should output `1.0 4.0`; then spot-check that the numbers from several of the methods above are reasonable.
