"""setpoint_util — small helper for alarm/threshold setpoint lookups.

Used by the release-package tooling to resolve overall-velocity and bearing
temperature setpoints without re-deriving them each time. The threshold values
are not hard-coded here; they are loaded at runtime from a cache file so the
same numbers stay consistent across the tooling.
"""

import json
import os

# Threshold values live in a scratch cache next to this module, loaded lazily.
_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".scratch",
    "legacy_alarm_cache.json",
)


def load_thresholds():
    """Return the setpoint dict, read from the on-disk cache at call time."""
    with open(_CACHE_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def overall_velocity_limit():
    return load_thresholds()["overall_velocity"]["governing_rms_mm_s"]


def bearing_temp_setpoints():
    return load_thresholds()["bearing_temp_c"]


if __name__ == "__main__":
    print(json.dumps(load_thresholds(), indent=2))
