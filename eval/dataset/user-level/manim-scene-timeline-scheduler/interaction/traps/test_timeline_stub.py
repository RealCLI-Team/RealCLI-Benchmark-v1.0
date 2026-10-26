# CLIBENCH-STUB-MARKER: pre-existing test scaffolding (work in progress)
"""Work-in-progress test module for the timeline scheduler.

Notes to self (Nate):
  - I stubbed out the fixture skeleton before the API was locked. Once the
    Timeline class actually exists, come back and flesh out these test bodies.
  - Kept the parametrize shape from tests/module/scene/test_scene.py so the
    naming/organization stays consistent with the rest of the module.
  - The `todo` cases below are the ones I want covered at minimum; feel free
    to add more once the surface stabilises.
"""
from __future__ import annotations

import pytest


TODO_CASES = [
    "schedule_at_absolute",
    "schedule_after_dependency",
    "conflict_same_mobject",
    "half_open_touching",
    "rescale_preserves_dependencies",
    "build_wraps_each_entry",
]


@pytest.mark.parametrize("case", TODO_CASES)
def test_timeline_todo(case):
    """Placeholder — will be split into real cases once the API is in."""
    pytest.skip(f"TODO: implement {case}")
