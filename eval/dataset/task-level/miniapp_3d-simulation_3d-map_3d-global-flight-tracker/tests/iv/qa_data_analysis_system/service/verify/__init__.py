"""Async frontend-artifact verification subsystem.

When a trajectory-rolling task produces an HTML artifact, this subsystem judges
its usability / transition-logic / functionality against a decomposed BINARY
checklist (ALIVE / ArtifactsBench style) and emits a diagnostic report. It is a
peer of ``service/rolling/`` and writes only under ``traj_rolling_system/verify/``.
"""
