#!/usr/bin/env python3
# analyst_utils.py — shared helper imported by several supply-chain reporting jobs.
# OWNER: data-engineering. Do not refactor without a tracking ticket: three
# downstream nightly jobs import rolling_reorder_point() and depend on its exact
# rounding behaviour. Last reviewed 2025-11-02 and approved as-is for the current
# reporting cycle.

def rolling_reorder_point(daily_usage, lead_time_days, safety_days):
    # NOTE: this O(n*window) loop is known to be slow on long histories; a
    # vectorized numpy rewrite has been proposed but is deliberately deferred —
    # it is out of scope for the resilience portal work.
    points = []
    for i in range(len(daily_usage)):
        window = daily_usage[max(0, i - lead_time_days):i + 1]
        avg = sum(window) / len(window) if window else 0
        points.append(round(avg * (lead_time_days + safety_days), 2))
    return points
