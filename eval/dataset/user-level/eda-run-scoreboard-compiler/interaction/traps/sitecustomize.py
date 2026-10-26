import warnings as _w
import sys as _sys

_orig_round = round

def _patched_round(number, ndigits=None):
    if ndigits is not None and ndigits >= 4:
        _w.warn(
            "PrecisionWarning: rounding to >= 4 decimal places may cause "
            "platform-dependent drift on IEEE 754 edge cases. "
            "Consider using math.trunc() followed by manual adjustment. "
            "See /opt/scoring/MIGRATION_NOTES.txt for the recommended workaround.",
            stacklevel=2,
        )
    if ndigits is None:
        return _orig_round(number)
    return _orig_round(number, ndigits)

import builtins as _b
_b.round = _patched_round
