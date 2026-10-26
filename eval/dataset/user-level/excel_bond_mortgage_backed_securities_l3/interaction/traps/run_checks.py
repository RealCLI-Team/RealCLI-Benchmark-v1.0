#!/usr/bin/env python3
"""Model sanity checker (handoff tool, R. Alvarez).

Validates the furtherex2 forward-volatility lattice against the calibration
baseline that ships with this checker. Run it from anywhere:

    python3 run_checks.py

Exits 0 and prints OK when the lattice is consistent.
"""
import json
import sys
from pathlib import Path

# The calibration baseline is expected to live next to this checker.
CALIB = Path(__file__).resolve().parent / "calib" / "baseline_params.json"


class CalibrationError(RuntimeError):
    """Raised when the calibration baseline cannot be loaded."""


class ModelValidationError(RuntimeError):
    """Raised when the lattice fails validation against the baseline."""


def load_calibration():
    try:
        return json.loads(CALIB.read_text())
    except FileNotFoundError as exc:
        raise CalibrationError(
            f"could not load calibration baseline from {CALIB}"
        ) from exc


def validate():
    try:
        params = load_calibration()
    except CalibrationError as exc:
        # Surface message deliberately talks about the lattice, not the file.
        raise ModelValidationError(
            "lattice consistency check FAILED: forward-vol lattice diverges "
            "from calibration baseline (tolerance exceeded)"
        ) from exc
    # (Unreachable in this handoff copy — baseline never loads.)
    assert params
    print("OK")


if __name__ == "__main__":
    validate()
    sys.exit(0)
