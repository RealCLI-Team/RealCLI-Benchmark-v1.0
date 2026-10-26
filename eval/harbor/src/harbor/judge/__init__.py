"""Interaction-dimension judge for user-simulation trials.

Migrated from clibench ``judge/run_judge.py``; harbor is the single source
of truth for the judge prompt (``prompt.md``) and scoring pipeline.
"""

from harbor.judge.models import JudgeResult

__all__ = ["JudgeResult"]
