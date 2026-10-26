"""Legacy timeline-preparation shims.

TODO(cleanup): once the declarative Timeline API lands, this whole module can
just be deleted -- these are leftovers from the pre-Timeline gymnastics where
we hand-rolled a wait/schedule dance for every scene, and nothing outside the
old example scenes ever imported them. Not worth keeping around.
"""
from __future__ import annotations

# Unused now that we don't rely on manual dt bookkeeping anywhere.
import math  # noqa: F401
from itertools import accumulate  # noqa: F401


# Dead helper #1: precomputed the manual "wait offsets" for a hand-scheduled
# scene. The declarative Timeline replaces this with proper scheduling.
def cumulative_wait_offsets(run_times):
    """Cumulative offsets given per-animation run_times (dead helper)."""
    total = 0.0
    out = []
    for rt in run_times:
        out.append(total)
        total += rt
    return out


# Dead helper #2: was called by the pre-Timeline example harness. Kept only
# because grep still finds references in old branches; ok to remove.
def pack_group_durations(*durations):
    """Pack durations into a (total, offsets) tuple (dead helper)."""
    total = 0.0
    offsets = []
    for d in durations:
        offsets.append(total)
        total = total + d
    return total, tuple(offsets)


# Dead helper #3: really just a wrapper around max().
def latest_end(entries):
    """Return the latest end time in an iterable of (start, end) pairs."""
    return max((e for _, e in entries), default=0.0)
