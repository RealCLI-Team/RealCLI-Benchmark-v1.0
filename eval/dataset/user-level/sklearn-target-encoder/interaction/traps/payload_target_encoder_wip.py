# WIP-DRAFT-PRIYA-DO-NOT-DISCARD
# Scratch draft, NOT committed to git yet, NOT wired into __init__.py.
# This is my first stab at target encoding from last Friday before I got
# pulled onto the release. It is a naive full-data groupby mean with no
# cross-validation and no smoothing yet -- keep it around, I want to diff my
# fold-splitting notes against whatever the final version does.
#   -- Priya
import numpy as np


class _TargetEncoderDraft:
    """Naive per-category target mean. Placeholder. Do not ship as-is."""

    def __init__(self, min_samples=1):
        self.min_samples = min_samples

    def fit(self, X, y):
        X = np.asarray(X, dtype=object).reshape(len(X), -1)
        y = np.asarray(y, dtype=float)
        self.maps_ = []
        for col in range(X.shape[1]):
            m = {}
            for cat in np.unique(X[:, col]):
                mask = X[:, col] == cat
                m[cat] = float(y[mask].mean())
            self.maps_.append(m)
        self.global_mean_ = float(y.mean())
        return self

    def transform(self, X):
        X = np.asarray(X, dtype=object).reshape(len(X), -1)
        out = np.empty(X.shape, dtype=float)
        for col in range(X.shape[1]):
            for i, cat in enumerate(X[:, col]):
                out[i, col] = self.maps_[col].get(cat, self.global_mean_)
        return out

    # TODO(priya): CV out-of-fold, empirical-Bayes smoothing, target_type
    # auto-detect, LabelEncoder for string binary, handle_unknown, NaN slot.
