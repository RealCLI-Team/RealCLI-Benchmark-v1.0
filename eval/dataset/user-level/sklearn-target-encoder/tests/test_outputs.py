"""Behavioral checks for the target-encoding transformer.

All expected values below are frozen golden data: they were computed once
from the behavioral contract (Micci-Barreca shrinkage target encoding with
an internal K-fold encoding scheme) and are embedded literally in this
file. Nothing here is recomputed from the state of the working tree.
"""

import inspect
import pickle

import numpy as np
import pytest
from numpy.testing import assert_allclose


# ---------------------------------------------------------------------------
# Frozen golden data
# ---------------------------------------------------------------------------

# Dog/cat/snake regression data set (88 samples, 1 feature).
GOLD_X = np.array([["dog"] * 20 + ["cat"] * 30 + ["snake"] * 38], dtype=object).T
GOLD_Y = np.array(
    [90.3] * 5 + [80.1] * 15 + [20.4] * 5 + [20.1] * 25 + [21.2] * 8 + [49] * 30
)
# smooth="auto" encodings aligned with categories ["cat", "dog", "snake"].
GOLD_AUTO_ENCODINGS = np.array([20.15001694243374, 82.58705994416918, 43.15381171787118])
GOLD_AUTO_TARGET_MEAN = 44.28522727272727

# Binary fit_transform golden: 80 samples, categories p/q/r, cv=4,
# smooth=1.0, shuffle=True, random_state=0.
GOLD_BIN_X = np.array([["p"] * 30 + ["q"] * 30 + ["r"] * 20], dtype=object).T
GOLD_BIN_Y = np.array(
    [1, 0, 1, 0, 1, 1, 1, 1, 0, 1, 0, 1, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 1, 1,
     0, 0, 1, 1, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 1, 1, 0, 0, 0, 1, 0,
     0, 0, 0, 1, 1, 1, 1, 0, 1, 0, 1, 0, 0, 0, 1, 1, 0, 0, 0, 1, 1, 0, 1,
     1, 0, 1, 1, 1, 0, 1, 0, 1, 0]
)
GOLD_BIN_OUT = np.array(
    [0.44583333333333336, 0.4310606060606061, 0.4310606060606061,
     0.4310606060606061, 0.4310606060606061, 0.5, 0.5, 0.4310606060606061,
     0.5, 0.49920634920634926, 0.4310606060606061, 0.4310606060606061,
     0.49920634920634926, 0.49920634920634926, 0.49920634920634926,
     0.49920634920634926, 0.5, 0.4310606060606061, 0.4310606060606061,
     0.49920634920634926, 0.5, 0.49920634920634926, 0.49920634920634926,
     0.49920634920634926, 0.44583333333333336, 0.5, 0.44583333333333336,
     0.5, 0.49920634920634926, 0.5, 0.49933333333333335,
     0.5517543859649123, 0.49933333333333335, 0.42, 0.4193333333333334,
     0.5517543859649123, 0.49933333333333335, 0.42, 0.5517543859649123,
     0.5517543859649123, 0.4193333333333334, 0.5517543859649123, 0.42,
     0.5517543859649123, 0.42, 0.4193333333333334, 0.4193333333333334,
     0.4193333333333334, 0.49933333333333335, 0.5517543859649123,
     0.4193333333333334, 0.42, 0.5517543859649123, 0.42,
     0.5517543859649123, 0.5517543859649123, 0.5517543859649123,
     0.49933333333333335, 0.49933333333333335, 0.5517543859649123,
     0.6333333333333333, 0.557843137254902, 0.6333333333333333,
     0.46770833333333334, 0.5302083333333334, 0.557843137254902,
     0.5302083333333334, 0.46770833333333334, 0.6333333333333333,
     0.5302083333333334, 0.5302083333333334, 0.5302083333333334,
     0.557843137254902, 0.46770833333333334, 0.6333333333333333,
     0.557843137254902, 0.46770833333333334, 0.6333333333333333,
     0.46770833333333334, 0.6333333333333333]
)

# Continuous fit_transform golden: 12 samples, 2 features, cv=3,
# smooth=0.5, shuffle=True, random_state=42.
GOLD_CONT_X = np.array(
    [["x", "u"], ["y", "v"], ["x", "u"], ["z", "v"], ["y", "u"], ["x", "v"],
     ["z", "u"], ["y", "v"], ["x", "u"], ["z", "v"], ["y", "u"], ["x", "v"]],
    dtype=object,
)
GOLD_CONT_Y = np.array(
    [1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5, 10.5, 11.5, 12.5]
)
GOLD_CONT_OUT = np.array(
    [[7.339285714285714, 5.625], [8.339285714285714, 7.767857142857143],
     [5.875, 7.125], [9.416666666666666, 7.916666666666667],
     [7.05, 6.583333333333333], [5.875, 7.767857142857143],
     [9.416666666666666, 6.583333333333333], [7.05, 7.916666666666667],
     [7.339285714285714, 5.625], [6.075, 6.8522727272727275],
     [5.625, 5.625], [5.875, 7.767857142857143]]
)

# Leave-one-out CV golden: 6 samples, 3 categories a/a/b/b/c/c, y = 1.5..6.5,
# smooth=0.0, cv=6, shuffle=False.  Each row is encoded from the other
# observation in its own category.
GOLD_LOO_X = np.array([["a"], ["a"], ["b"], ["b"], ["c"], ["c"]], dtype=object)
GOLD_LOO_Y = np.array([1.5, 2.5, 3.5, 4.5, 5.5, 6.5])
GOLD_LOO_OUT = np.array([2.5, 1.5, 4.5, 3.5, 6.5, 5.5])

# Deterministic 3-fold golden: cv=3, shuffle=False, smooth=1.0.  With
# shuffle=False the folds are contiguous slices so the encodings are stable
# under any re-implementation of the fold ordering.
GOLD_DET_X = np.array([["a"], ["a"], ["b"], ["b"], ["c"], ["c"]], dtype=object)
GOLD_DET_Y = np.array([10.0, 20.0, 30.0, 40.0, 50.0, 60.0])
GOLD_DET_OUT = np.array([45.0, 45.0, 35.0, 35.0, 25.0, 25.0])

# Larger shuffled fit_transform golden: 40 samples, 4 categories, cv=5,
# smooth=2.0, shuffle=True, random_state=7.
GOLD_SHUF_X = np.array([["a"] * 10 + ["b"] * 10 + ["c"] * 10 + ["d"] * 10],
                       dtype=object).T
GOLD_SHUF_Y = np.arange(40, dtype=float) + 0.5
GOLD_SHUF_FIRST10 = np.array(
    [8.159090909090908, 8.55625, 8.55625, 8.23125, 8.23125, 7.50625,
     7.479166666666667, 7.479166666666667, 7.479166666666667, 7.50625]
)
GOLD_SHUF_LAST10 = np.array(
    [32.025, 31.8125, 31.67361111111111, 31.8125, 31.67361111111111,
     32.25568181818182, 32.14204545454545, 31.67361111111111, 31.8125, 32.025]
)

# NaN-as-category golden: category ordering must be ["a", "b", nan], and the
# NaN bucket must show up in the encoder's private ``_missing_indices`` map.
GOLD_NAN_X = np.array([["a"], ["a"], [np.nan], [np.nan], [np.nan], ["b"]],
                      dtype=object)
GOLD_NAN_Y = np.array([1.0, 3.0, 5.0, 7.0, 9.0, 20.0])
GOLD_NAN_ENC = np.array([2.0, 20.0, 7.0])

# Pandas DataFrame contract golden.
GOLD_DF_OUT = np.array(
    [[3.125, 3.125], [3.875, 3.875], [3.125, 3.125], [3.875, 3.875],
     [3.125, 3.125], [3.875, 3.875]]
)


# ---------------------------------------------------------------------------
# Frozen reference: the internal K-fold encoding scheme for continuous
# targets (permutation + contiguous folds, exactly as the contract fixes it)
# ---------------------------------------------------------------------------


def _kfold_indices(n_samples, n_splits, seed):
    indices = np.random.RandomState(seed).permutation(n_samples)
    folds = np.array_split(indices, n_splits)
    for i in range(n_splits):
        test_idx = folds[i]
        train_idx = np.concatenate([folds[j] for j in range(n_splits) if j != i])
        yield train_idx, test_idx


def _reference_fit_transform(X, y, smooth, cv, seed):
    n_samples, n_features = X.shape
    out = np.empty((n_samples, n_features), dtype=np.float64)
    for train_idx, test_idx in _kfold_indices(n_samples, cv, seed):
        y_train = y[train_idx]
        y_mean = np.mean(y_train)
        for f_idx in range(n_features):
            cats = {}
            for i in train_idx:
                cats.setdefault(X[i, f_idx], []).append(y[i])
            for i in test_idx:
                v = X[i, f_idx]
                if v not in cats:
                    out[i, f_idx] = y_mean
                else:
                    s = sum(cats[v])
                    n = len(cats[v])
                    if smooth == "auto":
                        y_var = np.var(y_train)
                        mean_c = s / n
                        ssd = sum((t - mean_c) ** 2 for t in cats[v])
                        lam = y_var * n / (y_var * n + ssd / n)
                        out[i, f_idx] = lam * mean_c + (1 - lam) * y_mean
                    else:
                        out[i, f_idx] = (s + smooth * y_mean) / (n + smooth)
    return out


# ---------------------------------------------------------------------------
# Contract checks
# ---------------------------------------------------------------------------


def test_importable_from_preprocessing():
    from sklearn.preprocessing import TargetEncoder  # noqa: F401


def test_public_signature_defaults():
    from sklearn.preprocessing import TargetEncoder

    sig = inspect.signature(TargetEncoder.__init__)
    params = sig.parameters
    for name in ("categories", "target_type", "smooth", "cv", "shuffle",
                 "random_state"):
        assert name in params, f"missing constructor parameter {name!r}"
    assert params["categories"].default == "auto"
    assert params["target_type"].default == "auto"
    assert params["smooth"].default == "auto"
    assert params["cv"].default == 5
    assert params["shuffle"].default is True
    assert params["random_state"].default is None


def test_zero_smooth_equals_conditional_mean():
    from sklearn.preprocessing import TargetEncoder

    X = np.array([["a"], ["a"], ["b"], ["b"], ["b"], ["c"]], dtype=object)
    y = np.array([1.0, 3.0, 2.0, 4.0, 6.0, 10.0])
    enc = TargetEncoder(smooth=0.0, target_type="continuous").fit(X, y)
    enc_map = dict(zip(list(enc.categories_[0]), enc.encodings_[0]))
    assert enc_map["a"] == pytest.approx(2.0)
    assert enc_map["b"] == pytest.approx(4.0)
    assert enc_map["c"] == pytest.approx(10.0)
    out = enc.transform(np.array([["a"], ["b"], ["c"]], dtype=object))
    assert_allclose(out.ravel(), [2.0, 4.0, 10.0])


def test_fixed_smooth_shrinkage_formula():
    from sklearn.preprocessing import TargetEncoder

    X = np.array([["a"], ["a"], ["b"]], dtype=object)
    y = np.array([1.0, 3.0, 8.0])
    smooth = 2.0
    enc = TargetEncoder(smooth=smooth, target_type="continuous").fit(X, y)
    mean = 4.0
    enc_map = dict(zip(list(enc.categories_[0]), enc.encodings_[0]))
    assert enc_map["a"] == pytest.approx((4.0 + smooth * mean) / (2 + smooth))
    assert enc_map["b"] == pytest.approx((8.0 + smooth * mean) / (1 + smooth))
    assert enc.target_mean_ == pytest.approx(mean)


def test_auto_smooth_golden_values():
    from sklearn.preprocessing import TargetEncoder

    enc = TargetEncoder(smooth="auto").fit(GOLD_X, GOLD_Y)
    assert enc.target_type_ == "continuous"
    assert list(enc.categories_[0]) == ["cat", "dog", "snake"]
    assert enc.target_mean_ == pytest.approx(GOLD_AUTO_TARGET_MEAN)
    assert_allclose(enc.encodings_[0], GOLD_AUTO_ENCODINGS, rtol=1e-8)

    out = enc.transform(
        np.array([["dog"], ["cat"], ["snake"], ["unicorn"]], dtype=object)
    )
    assert_allclose(
        out.ravel(),
        [GOLD_AUTO_ENCODINGS[1], GOLD_AUTO_ENCODINGS[0],
         GOLD_AUTO_ENCODINGS[2], GOLD_AUTO_TARGET_MEAN],
        rtol=1e-8,
    )


def test_smooth_increases_shrinkage_towards_global_mean():
    from sklearn.preprocessing import TargetEncoder

    low = TargetEncoder(smooth=1.0).fit(GOLD_X, GOLD_Y)
    high = TargetEncoder(smooth=5000.0).fit(GOLD_X, GOLD_Y)
    for cat in ("cat", "dog", "snake"):
        idx = list(low.categories_[0]).index(cat)
        assert abs(high.encodings_[0][idx] - GOLD_AUTO_TARGET_MEAN) < abs(
            low.encodings_[0][idx] - GOLD_AUTO_TARGET_MEAN
        )


def test_unknown_category_maps_to_target_mean():
    from sklearn.preprocessing import TargetEncoder

    X = np.array([["a"], ["b"], ["a"], ["b"]], dtype=object)
    y = np.array([1.0, 2.0, 3.0, 4.0])
    enc = TargetEncoder(smooth=1.0, target_type="continuous").fit(X, y)
    out = enc.transform(np.array([["brand_new"], ["a"]], dtype=object))
    assert out[0, 0] == pytest.approx(enc.target_mean_)
    assert out[1, 0] != pytest.approx(enc.target_mean_)


def test_nan_is_a_regular_category():
    from sklearn.preprocessing import TargetEncoder

    X = np.array([["a"], [np.nan], ["a"], [np.nan], ["a"]], dtype=object)
    y = np.array([1.0, 5.0, 2.0, 6.0, 3.0])
    enc = TargetEncoder(smooth=0.0, target_type="continuous").fit(X, y)
    out = enc.transform(np.array([[np.nan]], dtype=object))
    assert out[0, 0] == pytest.approx(5.5)
    out2 = enc.transform(np.array([["a"]], dtype=object))
    assert out2[0, 0] == pytest.approx(2.0)


def test_binary_target_string_labels():
    from sklearn.preprocessing import TargetEncoder

    X = np.array([["a"], ["a"], ["b"], ["b"]], dtype=object)
    y = np.array(["yes", "no", "no", "no"], dtype=object)
    enc = TargetEncoder(smooth=0.0).fit(X, y)
    assert enc.target_type_ == "binary"
    # "no" -> 0, "yes" -> 1: category "a" has one yes one no -> 0.5
    enc_map = dict(zip(list(enc.categories_[0]), enc.encodings_[0]))
    assert enc_map["a"] == pytest.approx(0.5)
    assert enc_map["b"] == pytest.approx(0.0)
    assert enc.target_mean_ == pytest.approx(0.25)


def test_fit_transform_continuous_golden():
    from sklearn.preprocessing import TargetEncoder

    enc = TargetEncoder(smooth=0.5, cv=3, random_state=42)
    out = enc.fit_transform(GOLD_CONT_X, GOLD_CONT_Y)
    assert out.shape == (12, 2)
    assert out.dtype == np.float64
    assert_allclose(out, GOLD_CONT_OUT, rtol=1e-8, atol=1e-10)


def test_fit_transform_continuous_matches_frozen_reference():
    from sklearn.preprocessing import TargetEncoder

    rng = np.random.RandomState(3)
    letters = np.array(["a", "b", "c", "d"], dtype=object)
    X = letters[rng.randint(0, 4, size=(90, 2))].astype(object)
    y = rng.normal(size=90)
    for smooth in ("auto", 0.0, 2.5):
        enc = TargetEncoder(smooth=smooth, cv=4, random_state=11)
        out = enc.fit_transform(X, y)
        ref = _reference_fit_transform(X, y, smooth, 4, 11)
        assert_allclose(out, ref, rtol=1e-8, atol=1e-10)


def test_fit_transform_binary_golden():
    from sklearn.preprocessing import TargetEncoder

    enc = TargetEncoder(smooth=1.0, cv=4, random_state=0)
    out = enc.fit_transform(GOLD_BIN_X, GOLD_BIN_Y)
    assert out.shape == (80, 1)
    assert_allclose(out.ravel(), GOLD_BIN_OUT, rtol=1e-8, atol=1e-10)


def test_fit_transform_never_uses_own_target():
    from sklearn.preprocessing import TargetEncoder

    X = np.array([["a"], ["a"], ["b"], ["b"]], dtype=object)
    y = np.array([10.0, 20.0, 100.0, 200.0])
    enc = TargetEncoder(
        smooth=0.0, cv=2, shuffle=False, target_type="continuous"
    )
    out = enc.fit_transform(X, y)
    # rows 0-1 ("a") are encoded from rows 2-3 (both "b"): mean 150
    assert_allclose(out[:2, 0], 150.0)
    # rows 2-3 ("b") are encoded from rows 0-1 (both "a"): mean 15
    assert_allclose(out[2:, 0], 15.0)


def test_fit_then_transform_differs_from_fit_transform():
    from sklearn.preprocessing import TargetEncoder

    rng = np.random.RandomState(0)
    letters = np.array(["a", "b", "c"], dtype=object)
    X = letters[rng.randint(0, 3, size=(60, 1))].astype(object)
    y = rng.normal(size=60)
    out_ft = TargetEncoder(smooth=1.0, cv=5, random_state=0).fit_transform(X, y)
    out_t = TargetEncoder(smooth=1.0).fit(X, y).transform(X)
    assert not np.allclose(out_ft, out_t)


def test_fit_transform_reproducible():
    from sklearn.preprocessing import TargetEncoder

    rng = np.random.RandomState(0)
    letters = np.array(["a", "b", "c"], dtype=object)
    X = letters[rng.randint(0, 3, size=(60, 1))].astype(object)
    y = rng.normal(size=60)
    out1 = TargetEncoder(cv=4, random_state=13).fit_transform(X, y)
    out2 = TargetEncoder(cv=4, random_state=13).fit_transform(X, y)
    assert_allclose(out1, out2, rtol=0, atol=0)


def test_fitted_attributes_contract():
    from sklearn.preprocessing import TargetEncoder

    enc = TargetEncoder(smooth="auto").fit(GOLD_X, GOLD_Y)
    assert isinstance(enc.encodings_, (list, tuple))
    assert len(enc.encodings_) == 1
    assert enc.encodings_[0].shape == (len(enc.categories_[0]),)
    assert enc.encodings_[0].dtype == np.float64
    assert enc.n_features_in_ == 1
    assert enc.target_type_ == "continuous"
    names = enc.get_feature_names_out()
    assert list(names) == ["x0"]


def test_feature_names_dataframe():
    from sklearn.preprocessing import TargetEncoder

    pd = pytest.importorskip("pandas")
    df = pd.DataFrame({"city": ["a", "b", "a", "b", "a", "b"],
                       "color": ["r", "g", "r", "g", "r", "g"]})
    y = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    enc = TargetEncoder(smooth=1.0, target_type="continuous").fit(df, y)
    assert list(enc.get_feature_names_out()) == ["city", "color"]
    assert list(enc.feature_names_in_) == ["city", "color"]
    out = enc.transform(df)
    assert out.shape == (6, 2)
    assert out.dtype == np.float64
    assert_allclose(out, GOLD_DF_OUT, rtol=1e-10, atol=1e-12)


def test_multiclass_auto_target_raises():
    from sklearn.preprocessing import TargetEncoder

    X = np.array([["a"], ["b"], ["c"], ["a"], ["b"], ["c"]], dtype=object)
    y = np.array([0, 1, 2, 0, 1, 2])
    with pytest.raises(ValueError):
        TargetEncoder(target_type="auto").fit(X, y)


def test_invalid_params_raise():
    from sklearn.preprocessing import TargetEncoder

    X = np.array([["a"], ["b"]], dtype=object)
    y = np.array([1.0, 2.0])
    with pytest.raises(ValueError):
        TargetEncoder(smooth=-1.0).fit(X, y)
    with pytest.raises(ValueError):
        TargetEncoder(cv=1).fit(X, y)
    with pytest.raises(ValueError):
        TargetEncoder(target_type="multiclass").fit(X, y)


def test_constant_target_auto_smooth_falls_back_to_mean():
    from sklearn.preprocessing import TargetEncoder

    X = np.array([["a"], ["b"], ["a"], ["b"], ["c"]], dtype=object)
    y = np.full(5, 3.25)
    enc = TargetEncoder(smooth="auto", target_type="continuous").fit(X, y)
    for value in enc.encodings_[0]:
        assert value == pytest.approx(3.25)


def test_output_shape_and_dtype_multi_feature():
    from sklearn.preprocessing import TargetEncoder

    rng = np.random.RandomState(0)
    letters = np.array(["a", "b", "c", "d", "e"], dtype=object)
    X = letters[rng.randint(0, 5, size=(70, 3))].astype(object)
    y = rng.normal(size=70)
    enc = TargetEncoder()
    out = enc.fit_transform(X, y)
    assert out.shape == (70, 3)
    assert out.dtype == np.float64
    assert np.all(np.isfinite(out))
    out2 = enc.transform(X)
    assert out2.shape == (70, 3)


# ---------------------------------------------------------------------------
# Deep-integration checks — the encoder must live inside the existing
# scikit-learn categorical-encoder family, not stand off to the side.
# ---------------------------------------------------------------------------


def test_shares_base_class_with_ordinal_and_onehot_encoders():
    """TargetEncoder must share the ``_missing_indices`` bookkeeping
    convention used by the :class:`OrdinalEncoder` / :class:`OneHotEncoder`
    family.

    2026-09-19 QC fix: relaxed from an ``isinstance(TargetEncoder(),
    _BaseEncoder)`` mechanism assertion to a behavioral-contract check.
    The instruction and info slices pin the public surface to upstream
    sklearn 1.3's TargetEncoder (whose real class hierarchy does *not*
    include ``_BaseEncoder``) and only require that the *family* shares the
    missing-value bookkeeping — the refactoring mechanism was explicitly
    left to the implementer.  Failing spec-compliant upstream-style
    hierarchies was a false negative; the measurement target (shared
    bookkeeping + family sanity) is unchanged.
    """
    import numpy as np
    from sklearn.preprocessing import TargetEncoder, OrdinalEncoder, OneHotEncoder
    from sklearn.preprocessing._encoders import _BaseEncoder

    # Behavioral contract: fitting a column with NaN must populate the
    # shared -1-convention bookkeeping (missing bucket position per feature,
    # -1 for features without NaN), however it is wired up.
    enc = TargetEncoder(smooth=0.0, target_type="continuous").fit(
        np.array([["a"], [np.nan], ["b"]], dtype=object),
        np.array([1.0, 2.0, 3.0]),
    )
    assert hasattr(enc, "_missing_indices"), (
        "TargetEncoder must expose the shared _missing_indices bookkeeping "
        "of the OrdinalEncoder / OneHotEncoder family"
    )
    assert enc._missing_indices[0] == 2
    assert enc._missing_indices.get(1, -1) == -1
    # And OrdinalEncoder / OneHotEncoder must also still be _BaseEncoder
    # subclasses — sanity-checking that our modifications to _BaseEncoder
    # haven't accidentally broken the class hierarchy.
    assert isinstance(OrdinalEncoder(), _BaseEncoder)
    assert isinstance(OneHotEncoder(), _BaseEncoder)


def test_categories_alignment_with_ordinal_encoder():
    """The ``categories_`` layout must match what :class:`OrdinalEncoder`
    produces on the same input.  Two supervised encoders that both extend
    ``_BaseEncoder`` must agree on the sorted-with-nan-at-end convention."""
    from sklearn.preprocessing import TargetEncoder, OrdinalEncoder

    X = np.array([["dog"], ["cat"], ["snake"], ["dog"], ["cat"]], dtype=object)
    y = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    te = TargetEncoder(target_type="continuous").fit(X, y)
    oe = OrdinalEncoder().fit(X)
    assert list(te.categories_[0]) == list(oe.categories_[0])


def test_missing_indices_tracks_nan_bucket_position():
    """When TargetEncoder is fitted on a column containing NaN, it must
    populate a ``_missing_indices`` mapping that gives the position of the
    NaN category inside ``categories_[i]``.  Features without NaN must map
    to -1.  This is the shared plumbing that lets any encoder subclass
    address the missing-value bucket without having to rescan the array.
    """
    from sklearn.preprocessing import TargetEncoder

    X = np.array(
        [["a", "x"], [np.nan, "y"], ["a", "x"], [np.nan, "y"], ["b", "x"]],
        dtype=object,
    )
    y = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    enc = TargetEncoder(smooth=0.0, target_type="continuous").fit(X, y)

    assert hasattr(enc, "_missing_indices"), (
        "TargetEncoder must expose a _missing_indices mapping populated by "
        "the shared _BaseEncoder fitting pathway"
    )
    # Column 0 has NaN — categories_ end in NaN, so missing index is 2.
    assert enc._missing_indices[0] == 2
    # Column 1 has no NaN.
    assert enc._missing_indices[1] == -1


def test_missing_indices_all_features_without_nan():
    from sklearn.preprocessing import TargetEncoder

    X = np.array([["a", "x"], ["b", "y"], ["a", "x"]], dtype=object)
    y = np.array([1.0, 2.0, 3.0])
    enc = TargetEncoder(smooth=0.0, target_type="continuous").fit(X, y)
    assert enc._missing_indices == {0: -1, 1: -1}


def test_nan_encoding_matches_categorywise_mean():
    from sklearn.preprocessing import TargetEncoder

    enc = TargetEncoder(smooth=0.0, target_type="continuous").fit(
        GOLD_NAN_X, GOLD_NAN_Y
    )
    # categories_ must be ["a", "b", nan] (sorted objects with nan at end).
    cats = list(enc.categories_[0])
    assert cats[:2] == ["a", "b"]
    # Numpy nan != nan, hence we check via np.isnan on the last element.
    assert np.isnan(cats[2])
    assert_allclose(enc.encodings_[0], GOLD_NAN_ENC, rtol=1e-12)


# ---------------------------------------------------------------------------
# handle_unknown parameter — parallel to OrdinalEncoder's semantics.
# ---------------------------------------------------------------------------


def test_handle_unknown_default_is_ignore():
    from sklearn.preprocessing import TargetEncoder

    sig = inspect.signature(TargetEncoder.__init__)
    params = sig.parameters
    assert "handle_unknown" in params, (
        "TargetEncoder must expose a handle_unknown parameter mirroring "
        "OrdinalEncoder / OneHotEncoder"
    )
    assert params["handle_unknown"].default == "ignore"


def test_handle_unknown_error_raises_on_transform():
    from sklearn.preprocessing import TargetEncoder

    X = np.array([["a"], ["b"], ["a"], ["b"]], dtype=object)
    y = np.array([1.0, 2.0, 3.0, 4.0])
    enc = TargetEncoder(
        smooth=1.0, target_type="continuous", handle_unknown="error"
    ).fit(X, y)
    with pytest.raises(ValueError):
        enc.transform(np.array([["never_seen"]], dtype=object))


def test_handle_unknown_ignore_encodes_target_mean():
    from sklearn.preprocessing import TargetEncoder

    X = np.array([["a"], ["b"], ["a"], ["b"]], dtype=object)
    y = np.array([1.0, 2.0, 3.0, 4.0])
    enc = TargetEncoder(
        smooth=1.0, target_type="continuous", handle_unknown="ignore"
    ).fit(X, y)
    out = enc.transform(np.array([["never_seen"], ["a"]], dtype=object))
    assert out[0, 0] == pytest.approx(enc.target_mean_)
    # Known category must not use target mean.
    assert out[1, 0] != pytest.approx(enc.target_mean_)


def test_handle_unknown_invalid_value_raises():
    from sklearn.preprocessing import TargetEncoder

    X = np.array([["a"], ["b"]], dtype=object)
    y = np.array([1.0, 2.0])
    with pytest.raises(ValueError):
        TargetEncoder(handle_unknown="drop").fit(X, y)


# ---------------------------------------------------------------------------
# classes_ attribute — parity with scikit-learn classifiers on binary.
# ---------------------------------------------------------------------------


def test_binary_target_exposes_sorted_classes():
    from sklearn.preprocessing import TargetEncoder

    X = np.array([["a"], ["a"], ["b"], ["b"]], dtype=object)
    y = np.array(["yes", "no", "no", "no"], dtype=object)
    enc = TargetEncoder(smooth=0.0).fit(X, y)
    # LabelEncoder sorts labels alphabetically: no -> 0, yes -> 1.
    assert enc.classes_ is not None
    assert list(enc.classes_) == ["no", "yes"]


def test_binary_target_numeric_labels_classes_ordered():
    from sklearn.preprocessing import TargetEncoder

    X = np.array([["a"], ["a"], ["b"], ["b"]], dtype=object)
    y = np.array([7, 3, 7, 3])
    enc = TargetEncoder(smooth=0.0).fit(X, y)
    assert enc.target_type_ == "binary"
    assert list(enc.classes_) == [3, 7]


def test_continuous_target_has_no_classes():
    from sklearn.preprocessing import TargetEncoder

    X = np.array([["a"], ["b"], ["a"], ["b"]], dtype=object)
    y = np.array([1.5, 2.5, 3.5, 4.5])
    enc = TargetEncoder(smooth=0.0, target_type="continuous").fit(X, y)
    assert enc.classes_ is None


# ---------------------------------------------------------------------------
# fit_transform edge cases: leave-one-out, deterministic folds, shuffled runs.
# ---------------------------------------------------------------------------


def test_leave_one_out_fit_transform_golden():
    from sklearn.preprocessing import TargetEncoder

    enc = TargetEncoder(
        smooth=0.0, cv=6, shuffle=False, target_type="continuous"
    )
    out = enc.fit_transform(GOLD_LOO_X, GOLD_LOO_Y)
    assert_allclose(out.ravel(), GOLD_LOO_OUT, rtol=1e-12, atol=1e-12)


def test_deterministic_shuffle_false_golden():
    from sklearn.preprocessing import TargetEncoder

    enc = TargetEncoder(
        smooth=1.0, cv=3, shuffle=False, target_type="continuous"
    )
    out = enc.fit_transform(GOLD_DET_X, GOLD_DET_Y)
    assert_allclose(out.ravel(), GOLD_DET_OUT, rtol=1e-12, atol=1e-12)


def test_larger_shuffled_fit_transform_golden():
    from sklearn.preprocessing import TargetEncoder

    enc = TargetEncoder(
        smooth=2.0, cv=5, shuffle=True, random_state=7, target_type="continuous"
    )
    out = enc.fit_transform(GOLD_SHUF_X, GOLD_SHUF_Y).ravel()
    assert_allclose(out[:10], GOLD_SHUF_FIRST10, rtol=1e-8, atol=1e-10)
    assert_allclose(out[-10:], GOLD_SHUF_LAST10, rtol=1e-8, atol=1e-10)


# ---------------------------------------------------------------------------
# Explicit categories: unseen labels bucket to the global mean.
# ---------------------------------------------------------------------------


def test_explicit_categories_unseen_label_gets_target_mean():
    from sklearn.preprocessing import TargetEncoder

    X = np.array([["a"], ["a"], ["b"]], dtype=object)
    y = np.array([1.0, 2.0, 3.0])
    enc = TargetEncoder(
        categories=[["a", "b", "c", "d"]],
        smooth=0.0,
        target_type="continuous",
    ).fit(X, y)
    enc_map = dict(zip(list(enc.categories_[0]), enc.encodings_[0]))
    # a: mean 1.5; b: mean 3.0; c/d never seen so they get the global mean (2.0).
    assert enc_map["a"] == pytest.approx(1.5)
    assert enc_map["b"] == pytest.approx(3.0)
    assert enc_map["c"] == pytest.approx(enc.target_mean_)
    assert enc_map["d"] == pytest.approx(enc.target_mean_)


# ---------------------------------------------------------------------------
# Input-shape robustness: pandas Series with a non-default index, tuples, ...
# ---------------------------------------------------------------------------


def test_pandas_series_y_with_non_default_index():
    """A pandas Series ``y`` with a non-positional index must still be
    consumed positionally — otherwise the encoder silently mixes samples up
    (e.g. NaN-aligning on the DataFrame's own index).  This is the same
    convention every other supervised transformer follows.
    """
    from sklearn.preprocessing import TargetEncoder

    pd = pytest.importorskip("pandas")

    X = np.array([["a"], ["b"], ["a"], ["b"]], dtype=object)
    y = pd.Series([1.0, 2.0, 3.0, 4.0], index=[100, 200, 300, 400])
    enc = TargetEncoder(smooth=0.0, target_type="continuous").fit(X, y)
    enc_map = dict(zip(list(enc.categories_[0]), enc.encodings_[0]))
    assert enc_map["a"] == pytest.approx(2.0)
    assert enc_map["b"] == pytest.approx(3.0)


def test_y_as_python_list():
    from sklearn.preprocessing import TargetEncoder

    X = np.array([["a"], ["b"], ["a"], ["b"]], dtype=object)
    y_list = [1.0, 2.0, 3.0, 4.0]
    enc = TargetEncoder(smooth=0.0, target_type="continuous").fit(X, y_list)
    enc_map = dict(zip(list(enc.categories_[0]), enc.encodings_[0]))
    assert enc_map["a"] == pytest.approx(2.0)
    assert enc_map["b"] == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# Pickling round-trip: fitted transformers must serialize losslessly.
# ---------------------------------------------------------------------------


def test_pickle_round_trip_preserves_encoding():
    from sklearn.preprocessing import TargetEncoder

    X = np.array([["a"], ["b"], ["a"], ["b"]], dtype=object)
    y = np.array([1.0, 2.0, 3.0, 4.0])
    enc = TargetEncoder(smooth=1.0, target_type="continuous").fit(X, y)
    restored = pickle.loads(pickle.dumps(enc))
    assert restored.target_mean_ == pytest.approx(enc.target_mean_)
    assert_allclose(restored.encodings_[0], enc.encodings_[0], rtol=1e-12)
    assert_allclose(
        restored.transform(X), enc.transform(X), rtol=1e-12, atol=1e-14
    )


# ---------------------------------------------------------------------------
# Backward-compat guardrails: our modifications to _BaseEncoder must not
# regress the existing categorical encoders.
# ---------------------------------------------------------------------------


def test_ordinal_encoder_still_works():
    from sklearn.preprocessing import OrdinalEncoder

    X = np.array([["a"], ["b"], ["c"], ["a"], ["b"]], dtype=object)
    oe = OrdinalEncoder().fit(X)
    assert list(oe.categories_[0]) == ["a", "b", "c"]
    assert_allclose(oe.transform([["a"], ["b"], ["c"]]).ravel(), [0, 1, 2])


def test_one_hot_encoder_still_works():
    from sklearn.preprocessing import OneHotEncoder

    X = np.array([["a"], ["b"], ["a"], ["b"], ["a"]], dtype=object)
    oh = OneHotEncoder(sparse_output=False).fit(X)
    out = oh.transform([["a"], ["b"]])
    assert_allclose(out, [[1.0, 0.0], [0.0, 1.0]])


def test_one_hot_encoder_infrequent_still_works():
    """A regression check for the infrequent-category path in OneHotEncoder.
    Our extension to ``_BaseEncoder._fit`` is only supposed to bite when the
    ``return_and_ignore_missing_for_infrequent`` flag is set — the default
    behaviour used by OneHotEncoder must be unchanged.
    """
    from sklearn.preprocessing import OneHotEncoder

    X = np.array([["a"]] * 10 + [["b"]] * 3 + [["c"]], dtype=object)
    oh = OneHotEncoder(min_frequency=2, sparse_output=False).fit(X)
    # a and b are frequent; c is infrequent and grouped.
    assert oh.infrequent_categories_[0].tolist() == ["c"]


# ---------------------------------------------------------------------------
# get_params / set_params round-trip
# ---------------------------------------------------------------------------


def test_get_set_params_round_trip():
    from sklearn.preprocessing import TargetEncoder

    enc = TargetEncoder(smooth=1.5, cv=3, random_state=42, handle_unknown="error")
    params = enc.get_params()
    assert params["smooth"] == 1.5
    assert params["cv"] == 3
    assert params["random_state"] == 42
    assert params["handle_unknown"] == "error"

    clone = TargetEncoder().set_params(**params)
    assert clone.get_params() == params


def test_target_mean_is_python_float():
    """Downstream serialization (json, sql, dashboards) breaks when
    target_mean_ leaks a numpy scalar.  The rest of scikit-learn stores
    per-target aggregates as plain Python floats — this must, too.
    """
    from sklearn.preprocessing import TargetEncoder

    X = np.array([["a"], ["b"], ["a"], ["b"]], dtype=object)
    y = np.array([1.0, 2.0, 3.0, 4.0])
    enc = TargetEncoder(smooth=0.0, target_type="continuous").fit(X, y)
    assert type(enc.target_mean_) is float
