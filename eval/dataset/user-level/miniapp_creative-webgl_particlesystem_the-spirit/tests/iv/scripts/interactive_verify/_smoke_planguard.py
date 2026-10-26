#!/usr/bin/env python3
"""Golden-plan gate: refuse to sweep on a planner that reproduces b0811's
false-negative signatures.

Three defects cost 526 gated-to-zero tasks on b0811 and were invisible in the
scores — a plan that probes the wrong thing produces a confident wrong verdict,
not an error. Each is cheap to detect in the emitted plan, so detect it here and
fail loudly rather than discovering it again by hand-auditing verdicts:

  1. capture:"before_after" on a trailing `wait` — the frames bracket an
     already-finished state, so the check is judged on evidence of nothing
     happening (2,491/11,031 b0811 plans).
  2. a coordinate outside the 1280x720 viewport we actually drive — the probe
     lands on nothing (213/1,352 coordinate steps; 183 were the probe itself).
  3. a semantic duplicate probe — the same control clicked twice under two
     selector dialects, toggling it back off between the frames (395/5,088).

Run: `python scripts/interactive_verify/_smoke_planguard.py`, exit 0 == pass.
Not pytest (it crashes this server).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.interactive_verify import planner  # noqa: E402


def audit(actions: list[dict[str, Any]]) -> list[str]:
    """Defect signatures present in an emitted plan. Empty == clean.

    Importable: the sweep driver calls this per plan and counts the results
    into the run census, so a regression surfaces as a number in the report
    rather than as quietly wrong scores.
    """
    out: list[str] = []
    seen: set[tuple] = set()
    for i, a in enumerate(actions or []):
        verb, cap = a.get("action"), a.get("capture")
        if cap == "before_after":
            if verb == "wait":
                out.append(f"step {i}: capture on a `wait` (frames bracket nothing)")
            key = (a.get("for_item"), verb, planner.target_label(a.get("target")))
            if key in seen:
                out.append(f"step {i}: semantic duplicate probe {key}")
            seen.add(key)
        for field in ("from", "to", "at", "point"):
            pt = a.get(field)
            if isinstance(pt, (list, tuple)) and len(pt) >= 2 \
                    and not planner._in_viewport(pt):
                out.append(f"step {i}: {field}={list(pt)[:2]} outside "
                           f"{planner.DRIVE_VP[0]}x{planner.DRIVE_VP[1]}")
    return out


def main() -> int:
    # Each fixture is one real b0811 defect shape; audit must catch every one.
    dirty = [
        {"action": "drag", "target": "slider 'Beam power'", "for_item": "3"},
        {"action": "wait", "ms": 800, "capture": "before_after", "for_item": "3"},
        {"action": "click", "from": [640, 1040], "for_item": "4",
         "capture": "before_after"},
        {"action": "click", "target": "button 'DRIFT'", "for_item": "5",
         "capture": "before_after"},
        {"action": "click", "target": "button:has-text(\"DRIFT\")", "for_item": "5",
         "capture": "before_after"},
    ]
    found = audit(dirty)
    assert any("capture on a `wait`" in f for f in found), found
    assert any("outside 1280x720" in f for f in found), found
    assert any("semantic duplicate" in f for f in found), found
    assert len(found) == 3, f"expected exactly 3 defects, got {found}"

    # A clean plan of the shape the fixed generator/planner now emits.
    clean = [
        {"action": "drag", "target": "slider 'Beam power'", "for_item": "3",
         "capture": "before_after"},
        {"action": "wait", "ms": 800, "for_item": "3"},
        {"action": "click", "target": "button 'DRIFT'", "for_item": "5",
         "capture": "before_after"},
        {"action": "press", "key": "ArrowUp", "for_item": "6",
         "capture": "before_after"},
        {"action": "click", "from": [640, 400], "for_item": "7"},
    ]
    assert audit(clean) == [], audit(clean)

    # The planner's own output on a checklist that authored real targets must
    # itself be clean — this is the end-to-end form of the gate.
    items = [
        {"check_item_id": "1", "critical": True, "mode": "VLM",
         "checking_content": "the Symmetry slider changes the pattern",
         "action_plan": [{"action": "drag", "target": "slider 'Symmetry'",
                          "capture": "before_after"},
                         {"action": "wait", "ms": 600}]},
        {"check_item_id": "2", "critical": False, "mode": "DOM",
         "checking_content": "the Reset button restores defaults",
         "action_plan": [{"action": "click", "target": "button 'Reset'",
                          "capture": "before_after"}]},
    ]
    plan = planner.plan("build a symmetry toy",
                        {"ax": [], "url": "http://x/", "dom_snapshot": ""}, items,
                        lambda m: {"actions": [
                            {"action": "drag", "for_item": "1",
                             "from": [400, 980], "to": [900, 980],
                             "capture": "before_after"},
                            {"action": "wait", "ms": 500, "for_item": "1"},
                        ]})
    defects = audit(plan["actions"])
    assert defects == [], f"planner emitted a defective plan: {defects}"

    print(f"PASS: _smoke_planguard ({len(plan['actions'])} planned actions, clean)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
