#!/usr/bin/env python3
"""Put the QA service layer on sys.path.

This package lives under ``scripts/`` (importable as the ``scripts.*`` namespace
package from the repo root) but reuses ``service.*`` leaf modules, which resolve
only from ``qa_data_analysis_system/``. Call ``ensure_service_path()`` once at the
top of any module that imports ``service.*``, before those imports.
"""
from __future__ import annotations

import sys
from pathlib import Path

# scripts/interactive_verify/_bootstrap.py -> interactive_verify -> scripts -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
QA_ROOT = REPO_ROOT / "qa_data_analysis_system"


def ensure_service_path() -> Path:
    """Insert QA_ROOT onto sys.path (idempotent); return it."""
    if not (QA_ROOT / "service").is_dir():
        raise SystemExit(f"error: QA service layer not found under {QA_ROOT}")
    s = str(QA_ROOT)
    if s not in sys.path:
        sys.path.insert(0, s)
    return QA_ROOT
