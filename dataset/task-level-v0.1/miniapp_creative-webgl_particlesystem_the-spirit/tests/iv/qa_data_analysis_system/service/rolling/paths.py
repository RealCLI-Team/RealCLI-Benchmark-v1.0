#!/usr/bin/env python3
"""Shared on-disk locations for the rolling subsystem.

Everything the rolling system writes lives under ``traj_rolling_system/`` — a
sibling of the ``qa_data_analysis_system/`` package dir, at the repo root — so
rolling outputs are never mixed into ``jobs/`` or the published reports.

The tree happens to sit on the shared ``/volume/pt-coder`` mount, which is the
same volume the remote LiteLLM pod mounts; that is what lets the capturing proxy
append to a ``capture.jsonl`` this backend can read.
"""

from __future__ import annotations

from pathlib import Path

# service/rolling/paths.py -> service/rolling -> service -> qa_data_analysis_system
PKG_DIR = Path(__file__).resolve().parent.parent.parent
REPO_ROOT = PKG_DIR.parent
ROLLING_ROOT = REPO_ROOT / "traj_rolling_system"

SUBSETS_DIR = ROLLING_ROOT / "subsets"
RUNS_DIR = ROLLING_ROOT / "runs"

_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


def safe_slug(value: str, fallback: str = "x", maxlen: int = 80) -> str:
    """Filesystem-safe slug: keep [A-Za-z0-9-_.], no path traversal / dotfiles."""
    cleaned = "".join(c if c in _SAFE else "-" for c in (value or "").strip())
    cleaned = cleaned.replace("..", "-").strip(".-")
    return cleaned[:maxlen] or fallback


def resolve_out_path(out_path: str, default_name: str, suffix: str,
                     directory: bool = False) -> Path:
    """Resolve/validate a user-supplied output path for an export.

    Relative paths resolve under ``ROLLING_ROOT`` and may not escape it via
    ``..``. Absolute paths are allowed as-is.

    File mode (``directory=False``, the default): if the resolved path is an
    existing directory OR lacks ``suffix`` (case-insensitive), derive
    ``<path>/<safe_slug(default_name)><suffix>``. Creates the parent directory.
    Shared by the subset JSONL export (``.jsonl``) and the run ZIP export
    (``.zip``) so both enforce one path policy.

    Directory mode (``directory=True``): the resolved path IS the target
    directory, returned verbatim and created. It must resolve stably across
    repeated calls — the Harbor export re-runs into the same tree to skip or
    overwrite existing instances, so the file-mode "nest inside an existing
    dir" rule (which would turn ``ow`` into ``ow/ow`` on the second call) must
    not apply. ``suffix`` is ignored.

    Raises ``ValueError`` on empty/traversal input.
    """
    s = (out_path or "").strip()
    if not s:
        raise ValueError("output path is required")
    p = Path(s).expanduser()
    if p.is_absolute():
        target = p.resolve()
    else:
        base = ROLLING_ROOT.resolve()
        target = (base / p).resolve()
        if target != base and base not in target.parents:
            raise ValueError("relative output path escapes the rolling root")

    if directory:
        target.mkdir(parents=True, exist_ok=True)
        return target

    if target.is_dir() or target.suffix.lower() != suffix.lower():
        slug = safe_slug(default_name or "export", fallback="export")
        target = target / f"{slug}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def run_dir(run_id: str) -> Path:
    return RUNS_DIR / safe_slug(run_id, fallback="run")


def task_dir(run_id: str, record_key: str) -> Path:
    return run_dir(run_id) / "tasks" / record_key
