#!/usr/bin/env python3
"""Resolve rolling-run artifacts + embedded checklists into scoreable tasks.

Subset records are only {id, line_no}; the embedded checklist lives in the source
JSONL the subset points at. The rolling task dir is named by that same id and
holds workspace/ + result.json. This module bridges the two.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.interactive_verify._bootstrap import ensure_service_path
from scripts.interactive_verify.static_server import to_http_url

ensure_service_path()
from service.rolling import paths as rolling_paths  # noqa: E402
from service.verify import checklist as _checklist  # noqa: E402
from service.verify.htmleval_exec import _pick_entry  # noqa: E402

_CODE_SUFFIXES = (".html", ".htm", ".css", ".js")
_CODE_CAP = 40000


class TaskLoadError(RuntimeError):
    """Raised when a task can't be assembled (no HTML entry or no checklist)."""


class TaskInputMissing(TaskLoadError):
    """The task's INPUT is not on disk — an environment fault, not a task property.

    The distinction matters because the two are recorded differently. A tar that
    contains no HTML is a fact about the artifact: score it 0 and move on. A tar
    that is *not there* says nothing about the artifact at all, and writing a
    0.0/renderable:False verdict for it is a lie that the existence-based resume
    then makes permanent — the task is never retried, and the zero lands in the
    selection report as if it had been measured.

    This is not hypothetical. On b0811/v011 an orphaned run_harbor outlived the
    driver that owned its scratch tree; the tree was cleaned underneath it, and
    387 of 393 instances were written as `load: artifact tar not found` zeros in
    a seven-minute burst. Every one of those artifacts is fine. Raising this type
    instead lets run.py skip persistence, so the retry re-does them.
    """


def prompt_of(record: dict[str, Any]) -> str:
    msgs = record.get("messages") or []
    for m in reversed(msgs):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                return " ".join(b.get("text", "") for b in c if isinstance(b, dict))
    return ""


def index_source(source_path: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    p = Path(source_path)
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        rec = json.loads(ln)
        rid = str(rec.get("id"))
        if rid:
            out[rid] = rec
    return out


def _subset_path(subset_id: str, owner: str) -> Path:
    return rolling_paths.SUBSETS_DIR / owner / f"{subset_id}.json"


def resolve_source_path(subset_id: str | None, owner: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    if not subset_id:
        raise TaskLoadError("need --source or --subset to locate the checklist source")
    d = json.loads(_subset_path(subset_id, owner).read_text(encoding="utf-8"))
    ip = ((d.get("source") or {}).get("input_path"))
    if not ip:
        raise TaskLoadError(f"subset {subset_id} has no source.input_path")
    return ip


def _subset_ids(subset_id: str, owner: str) -> set[str]:
    d = json.loads(_subset_path(subset_id, owner).read_text(encoding="utf-8"))
    return {str(r["id"]) for r in d.get("records", []) if r.get("id")}


def list_task_ids(run_id: str, subset_id: str | None, owner: str) -> list[str]:
    tasks_root = rolling_paths.run_dir(run_id) / "tasks"
    if not tasks_root.is_dir():
        raise TaskLoadError(f"run {run_id} has no tasks dir at {tasks_root}")
    ids = sorted(p.name for p in tasks_root.iterdir() if p.is_dir())
    if subset_id:
        want = _subset_ids(subset_id, owner)
        ids = [i for i in ids if i in want]
    return ids


def gather_code(entry_path: Path) -> str:
    parts: list[str] = []
    ws = entry_path.parent
    files = [entry_path] + sorted(
        p for p in ws.rglob("*")
        if p.is_file() and p.suffix.lower() in _CODE_SUFFIXES and p != entry_path)
    total = 0
    for f in files:
        try:
            body = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        chunk = f"\n===== {f.relative_to(ws)} =====\n{body}"
        parts.append(chunk)
        total += len(chunk)
        if total >= _CODE_CAP:
            break
    return "".join(parts)[:_CODE_CAP]


def _load_from_workspace(task_id: str, ws: Path, record: dict[str, Any]) -> dict[str, Any]:
    entry = _pick_entry(ws)
    if entry is None:
        raise TaskLoadError(f"{task_id}: no HTML entry in workspace")
    raw_items = record.get("checklist") or []
    if not raw_items:
        raise TaskLoadError(f"{task_id}: no embedded checklist")
    items = _checklist.validate_checklist(raw_items)
    return {
        "task_id": task_id,
        "prompt": prompt_of(record),
        "items": items,
        "workspace": str(ws),
        # Local HTTP, not file:// — see static_server.py: module/importmap
        # artifacts are CORS-blocked under file:// and never boot.
        "entry_url": to_http_url(entry),
        "entry_path": str(entry),
        "code": gather_code(entry),
    }


def load_task(run_id: str, task_id: str, source_index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    record = source_index.get(task_id)
    if record is None:
        raise TaskLoadError(f"{task_id}: not found in source index")
    ws = rolling_paths.task_dir(run_id, task_id) / "workspace"
    if not ws.is_dir():
        raise TaskLoadError(f"{task_id}: no workspace dir at {ws}")
    return _load_from_workspace(task_id, ws, record)
