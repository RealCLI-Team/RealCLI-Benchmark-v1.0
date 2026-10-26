# High-cardinality string features are crashing pipeline training

I maintain my team's own fork of the scikit-learn development source under `/app` — it's already installed into the current Python in editable mode, so editing `/app/sklearn/**/*.py` takes effect immediately; after changing `.pyx` files, recompile with `python setup.py build_ext --inplace -j 4`.

## What happened to me

We've been doing user-attribute modeling lately, and more and more high-cardinality string columns are showing up in the feature table — `city`, `user_agent`, `product_sku` — each with tens of thousands of distinct values. We used to run `OneHotEncoder` first and then feed the result to downstream models, but now the dimensionality explodes into the hundreds of thousands and training straight-up OOMs.

A colleague recommended: "just use target mean encoding — replace each category with 'a shrunk estimate of the target mean for that category', one column stays one column." I went to `sklearn.preprocessing` to write `from sklearn.preprocessing import TargetEncoder` and found the thing simply doesn't exist. Code on our other branches already imports it this way in CI — can you fill in this component?

## How I expect to use it

It should work roughly like the following, out of the box. Both regression and binary classification must hold up:

```python
from sklearn.preprocessing import TargetEncoder
import numpy as np

X = np.array([["dog"] * 20 + ["cat"] * 30 + ["snake"] * 38], dtype=object).T
y = [90.3] * 5 + [80.1] * 15 + [20.4] * 5 + [20.1] * 25 + [21.2] * 8 + [49] * 30

enc = TargetEncoder().fit(X, y)
enc.transform([["dog"], ["cat"], ["snake"], ["unicorn"]])
# → 类别对应的收缩均值；从没见过的 "unicorn" 落到全局均值

# 团队大部分成员喜欢用 fit_transform 直接把训练集接进 pipeline
enc = TargetEncoder(smooth=1.0, cv=5, random_state=0)
X_train_enc = enc.fit_transform(X, y)
```

## The feel I care about

- It should be "family" with `OrdinalEncoder` and `OneHotEncoder`: obtainable from the top level of `sklearn.preprocessing` the same way; with attributes like `categories_`, `n_features_in_`, `get_feature_names_out()`; the constructor signature and public attributes should line up with upstream sklearn 1.3's `TargetEncoder` — `TargetEncoder(categories="auto", target_type="auto", smooth="auto", cv=5, shuffle=True, random_state=None)`, with fitted attributes including `categories_`, `n_features_in_`, `encodings_`, and `target_type_`; when parameters are invalid (e.g. a negative smoothing strength, a `cv=1`, an unrecognized `target_type`) it raises a clear `ValueError` at `fit` time, going through the project's existing unified validation machinery; `get_params()` / `set_params()` round-trip cleanly. DataFrame column names must be passed through exactly as given in the input.
- The smoothing strength can either be given explicitly as a non-negative float (larger values pull harder toward the global mean), or given as `"auto"` to let it pick one itself via empirical Bayes. We expect the formulas to match the classic implementation: with explicit smoothing, a category's encoded value is `(n * 类别均值 + smooth * 全局均值) / (n + smooth)` (n is the number of samples in that category); with `"auto"`, it's Micci-Barreca's empirical-Bayes shrinkage: 对每个类别 k 各算一个收缩权重 `λ_k = (var(y) * n_k) / (var(y) * n_k + 类别内离差平方和 / n_k)`（n_k 是该类别的样本数，λ 不是整列池化的一个标量），encoded value `λ_k * 类别均值 + (1 - λ_k) * 全局均值`；var(y) 取训练 y 的总体方差（ddof=0）; at `fit` time the global quantities are computed over the whole training y, and at `fit_transform` time each fold computes them over that fold's training portion. @jjerphan specifically insisted that the `"auto"` path must not produce NaNs on pathological inputs such as "a category that appears only once", "target variance is 0", or "the targets in some fold's training portion are all identical".
- The target type should be auto-detected by default. Regression targets (continuous floats) and binary classification targets (two classes, strings or numbers both fine) must both be handled smoothly; anything that looks like multiclass (3 or more discrete values) is expected to raise a clear error at `fit` time.
- Even when a binary classification target is made of strings ("yes"/"no" and the like), run it through the project's `LabelEncoder` before computing — same ordering convention as the other classifiers; equivalently, some attribute should reveal which original label was mapped to 0 and which to 1 (because downstream models' `predict_proba` follows that same order).
- Categories never seen during training: `transform` by default should give a sensible fallback (in most of our scenarios we just want it to land on the "global target mean" — we don't want a whole column of NaNs or an error). Control this through a `handle_unknown` parameter — our own extension mirroring `OrdinalEncoder`, not something upstream 1.3's `TargetEncoder` has: the default `"ignore"` gives the global-mean fallback above, while `"error"` switches it into "`ValueError` on unseen categories" mode, like `OrdinalEncoder` — in production we want this case to warn explicitly rather than be silently replaced; an unrecognized `handle_unknown` value must raise `ValueError` at `fit` time. Also expose a `classes_` attribute: for a binary target it holds the two sorted original labels (matching the `LabelEncoder` mapping order mentioned above), and for a continuous target it is `None`.
- Treat missing values (`np.nan`, `None`) as just an "ordinary category" with its own target mean encoding — don't raise, and don't treat them as unknown. Also, I want the encoder to internally know the position of the NaN slot in `categories_` (needed later for extensions like `min_frequency`), without having to re-scan every time — after `fit`, expose a `_missing_indices` mapping on the encoder (a dict whose keys are feature indices and whose values are the position of that feature's NaN slot within `categories_[i]`; record `-1` for a column with no NaN), and the whole `_BaseEncoder` family of encoders should share this bookkeeping.
- `fit(X, y).transform(X)` yields the steady-state "one number per category" encoding; but I want `fit_transform(X, y)` to give me encoded values computed **without having seen that row's own target** — internally it does a cross-validation split: permute the sample indices once using `random_state`, then `array_split` the shuffled indices into `cv` consecutive folds, encode each fold using statistics from the remaining folds (categories unseen within a fold fall back to the global mean of that fold's training portion), and the result fed to any sklearn model must not leak. Same input + same `random_state` must produce identical results across two `fit_transform` calls.
- The target may be a `numpy.ndarray`, a Python `list`, or a `pandas.Series` with a non-default index — the last one is the easiest to get wrong, because aligning on the index would scramble the sample order; be sure to consume it positionally.
- The `target_mean_` attribute is something we write to a dashboard, so it should be a plain Python `float` — numpy scalars keep causing trouble when serialized to JSON / databases.
- A fitted encoder must be picklable, and after unpickling it must keep being able to `transform` with results identical to before — in production the encoder is frozen into an artifact and reused over and over.

## Environment notes

- All changes happen under `/app`. scikit-learn is installed in editable mode, so Python source changes take effect immediately. If you choose to write the low-level loops in Cython (`.pyx`), remember to recompile the extensions under `/app`, and don't forget to sync the extension module declarations in the build scripts.
- The environment is offline — don't install anything from the network; common dependencies such as `numpy`, `scipy`, `pandas`, and `Cython` are already installed.
- Your changes are expected to **only add, never remove** capability — don't break the existing usage of `OrdinalEncoder` / `OneHotEncoder`: every pipeline we run in production depends on the existing behavior of these two classes, and any regression will be stopped by the CI lights.
