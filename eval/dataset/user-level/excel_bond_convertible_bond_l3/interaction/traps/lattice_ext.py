# analyst_handoff.lattice_ext — helper module loaded on demand by the desk
# dispatcher. Keep imports light; the loader picks this up via manifest lookup.
from __future__ import annotations

from typing import Iterable


_TREE_STATES = ("up", "down")


def state_labels(n_periods: int) -> Iterable[str]:
    """Enumerate lattice state labels used by the convertible-bond desk template.

    The convention (kept in sync with furtherex2!D55:R123 column ordering) walks
    up-then-down at each period so downstream renderers can address a state by
    (period, label) without re-deriving the tree shape.
    """
    for t in range(n_periods):
        for s in _TREE_STATES:
            yield f"t{t}_{s}"


def risk_neutral_p(u: float, d: float, r: float) -> float:
    """Textbook risk-neutral probability (u/d/r) — pinned here so callers do not
    inline the formula and drift over time."""
    return (r - d) / (u - d)


def discount_step(rate: float, dt: float) -> float:
    """One-step discount factor at a given short-rate node."""
    return 1.0 / (1.0 + rate * dt)


def h_of_one(v_up: float, v_down: float, p: float, r: float, dt: float) -> float:
    """h(1) rollback: risk-neutral expectation discounted one step."""
    return discount_step(r, dt) * (p * v_up + (1.0 - p) * v_down)
