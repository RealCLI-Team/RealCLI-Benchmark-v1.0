#!/usr/bin/env python3
"""Aggregate judged checklist items into a verdict.

Binary, execution-grounded scoring (the ALIVE principle): each item is satisfied
or not; the verdict is the weighted fraction satisfied. Two rules that guard
against silent over-scoring:

  * **Indeterminate items** (judge couldn't decide — ``satisfied is None``) are
    EXCLUDED from the denominator, never counted as satisfied.
  * **Renderability gate:** if the artifact does not render, the score is forced
    to 0 regardless of item outcomes (non-renderable code cannot pass).
  * **Unmeasured artifacts:** when NOTHING was decided the score is 0.0 by empty
    denominator, which reads identically to "everything failed". ``gate_reason``
    is set to ``no determinate evidence`` so selection can tell the two apart.

Pure — no I/O, no network.
"""

from __future__ import annotations

from typing import Any


def _bucket(judged: list[dict[str, Any]]) -> dict[str, Any]:
    num = den = 0.0
    sat = det = 0
    for it in judged:
        s = it["result"]["satisfied"]
        if s is None:
            continue
        w = float(it.get("weight", 1.0))
        den += w
        det += 1
        if s:
            num += w
            sat += 1
    score = (num / den) if den > 0 else 0.0
    return {"score": score, "satisfied": sat, "determinate": det}


def _group(judged: list[dict[str, Any]], key: str) -> dict[str, Any]:
    out: dict[str, list] = {}
    for it in judged:
        out.setdefault(it.get(key, ""), []).append(it)
    return {k: _bucket(v) for k, v in out.items()}


def aggregate(judged: list[dict[str, Any]], renderable: bool) -> dict[str, Any]:
    overall = _bucket(judged)
    indeterminate = sum(1 for it in judged if it["result"]["satisfied"] is None)
    score = overall["score"] if renderable else 0.0
    gate_reason = None if renderable else "artifact does not render"
    # Critical-item hard gate: a MUST-PASS behavior that is determinately
    # unsatisfied makes the artifact unusable regardless of how many features
    # render (a car that reverses on the forward key still "moves"). Force the
    # score to 0, mirroring the renderability gate. Only a determinate False
    # gates — an indeterminate critical (judge couldn't decide, e.g. gameplay
    # never reached) must NOT gate, or a start-failure would masquerade as a
    # control defect. Renderability precedence: keep its reason if already set.
    if renderable:
        failed_critical = next(
            (it for it in judged
             if it.get("critical") and it["result"]["satisfied"] is False), None)
        if failed_critical is not None:
            score = 0.0
            gate_reason = f"critical:{failed_critical.get('title', '')}"
        # Nothing was decided about this artifact at all. `_bucket` already
        # returns 0.0 here (empty denominator), which is arithmetically right and
        # semantically wrong: it makes "we learned nothing" indistinguishable from
        # "every item failed", and downstream selection reads both as a broken
        # artifact. Measured on the arena calibration: 10 of 32 arm-tasks scored
        # 0.0 with determinate == 0, and the human had passed a MEDIAN 73% of
        # their items — these are the largest remaining false zeros.
        #
        # The score stays 0.0 rather than becoming None: every consumer types it
        # as a float and a null would be a schema change. The gate_reason is what
        # carries the distinction, so a report can bucket these rows separately
        # instead of pooling them with real failures. Set only when no other gate
        # fired — a determinate critical failure is the more specific fact.
        elif overall["determinate"] == 0 and judged:
            gate_reason = "no determinate evidence"
    return {
        "score": score,
        "renderable": renderable,
        "total_items": len(judged),
        "determinate": overall["determinate"],
        "indeterminate": indeterminate,
        "satisfied": overall["satisfied"],
        "by_tier": _group(judged, "item_type"),
        "by_mode": _group(judged, "mode"),
        "gate_reason": gate_reason,
    }
