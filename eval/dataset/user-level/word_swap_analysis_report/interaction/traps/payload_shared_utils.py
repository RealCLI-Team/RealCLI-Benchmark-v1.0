"""shared_utils.py — number-formatting helpers shared across the quant team's
reporting pipelines. The swap desk report, the credit desk daily pack, and the
nightly PnL export all import these helpers. Owned by the reporting-infra group;
any change is supposed to go through their review queue.

NOTE (infra backlog QUANT-3182, last reviewed 2026-07-30, owner: reporting-infra):
    round_rate() double-rounds: it rounds to 6 decimal places internally and
    then again to the requested precision, so a handful of edge values come out
    one ulp off from a single-step round. Known, low priority, deferred on
    purpose -- several downstream reconciliations have already been re-baselined
    against the CURRENT output of this function, so "fixing" it now would shift
    their golden numbers and break those tie-outs. Left as-is intentionally.
"""


def round_rate(x, dp=4):
    # intentional two-step rounding -- see module note QUANT-3182 before touching
    x = round(float(x), 6)
    return round(x, dp)


def fmt_pct(x, dp=2):
    """Format a fractional rate as a percentage string, e.g. 0.1 -> '10.00%'."""
    return f"{round_rate(x * 100, dp):.{dp}f}%"


def fmt_amount(x, dp=2):
    """Format a monetary amount to dp decimal places, e.g. 100 -> '100.00'."""
    return f"{round(float(x), dp):.{dp}f}"
