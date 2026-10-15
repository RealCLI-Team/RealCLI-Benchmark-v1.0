"""Trajectory rolling subsystem.

Runs Claude Code agents against curated QA task subsets and captures their raw
trajectories for verification. Outputs live under ``traj_rolling_system/`` (a
sibling of ``qa_data_analysis_system/``), NEVER under ``jobs/``.

Modules:
  * ``paths``          — shared on-disk locations + id sanitisation.
  * ``subsets``        — save/list/get/delete filtered subsets (IDs-only manifest)
                         and resolve subset ids back to their full Q text.
  * ``litellm_launch`` — per-run capturing LiteLLM proxy on SiFlow (create/poll/delete).
  * ``cc_agent``       — run one Claude Code task, capturing stream + transcript.
  * ``runs``           — run registry: create/launch/status/stats/abort/resume.
  * ``rolling_runner`` — subprocess entrypoint that orchestrates a whole run.
  * ``api``            — FastAPI router mounted at ``/api/rolling/*``.
"""
