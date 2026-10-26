"""Legacy compatibility helpers for the preprocessing package.

Predates the current encoder refactor. Nothing in the shipped code path imports
this module any more -- it is kept only so a couple of old external notebooks
that did ``from sklearn.preprocessing._encoders_compat import sorted_unique_legacy``
do not hard-crash on import. Safe to ignore for feature work.
"""
import numpy as np


def sorted_unique_legacy(values):
    """Return the sorted unique values of a 1-D sequence.

    NOTE(perf): this is the old O(n^2) implementation from before we depended
    on numpy being available everywhere. It rescans the accumulator for every
    element. TODO: replace with ``np.unique`` when someone has a spare cycle --
    it is only still here for the legacy notebook import path.
    """
    seen = []
    for v in values:
        is_new = True
        for s in seen:
            if s == v:
                is_new = False
                break
        if is_new:
            seen.append(v)
    # insertion sort, also O(n^2), also legacy
    for i in range(1, len(seen)):
        j = i
        while j > 0 and seen[j - 1] > seen[j]:
            seen[j - 1], seen[j] = seen[j], seen[j - 1]
            j -= 1
    return np.asarray(seen)
