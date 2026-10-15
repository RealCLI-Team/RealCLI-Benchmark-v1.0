#!/usr/bin/env python3
"""Canonical checklist item: schema, validation, JSONL IO, dedup, merge.

A checklist item is the atomic unit the verifier scores. This module is the
on-disk contract for BOTH uploaded and model-generated checklists — every item,
wherever it came from, is normalised through :func:`normalize_item` before use.
Pure (no I/O beyond the JSONL helpers, no network) so it is fully unit-testable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

VALID_MODES = {
    "LLM Judge",
    "VLM Judge",
    "LLM-Based Browser Agent Judge",
    "VLM-Based Browser Agent Judge",
}
VALID_TYPES = {"general", "task-specific", "artifact-specific"}
_REQUIRED = ("task_id", "check_item_id", "checking_content", "mode", "item_type",
             "expected_result")


def _coerce_action_plan(raw: Any) -> list[dict[str, Any]]:
    """A check's ordered interaction steps. Keep only dict elements verbatim
    (step-shape is the planner's contract, not the normalizer's); anything that
    is not a list of dicts degrades to []."""
    if not isinstance(raw, list):
        return []
    return [s for s in raw if isinstance(s, dict)]


def normalize_item(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a canonical item; raise ``ValueError`` on any missing/invalid field.

    The canonical schema is the UNION of the fields the verifier requires and the
    richer fields our authored/benchmark checklists carry, so items from either
    source round-trip losslessly:

      * required — ``task_id, check_item_id, checking_content, mode, item_type,
        expected_result``.
      * ``weight`` — float, default ``1.0``; consumed by ``scoring`` (a check's
        share of the verdict). Optional so legacy items still load.
      * ``title`` — short human-readable summary of the check. Optional (default
        ""); populated by the generator and shown in reports/planner context.
      * ``origin`` — provenance tag (generated / benchmark / fixed / ...).
      * ``raw`` — opaque per-source passthrough (e.g. WorldCoder's judge_family /
        category / prompt_refs). Preserved verbatim when present so a benchmark's
        native metadata survives normalization; dropped when absent.
    """
    for f in _REQUIRED:
        if f not in raw or raw[f] in (None, ""):
            raise ValueError(f"checklist item missing required field: {f}")
    if raw["mode"] not in VALID_MODES:
        raise ValueError(f"invalid mode: {raw['mode']!r}")
    if raw["item_type"] not in VALID_TYPES:
        raise ValueError(f"invalid item_type: {raw['item_type']!r}")
    try:
        weight = float(raw.get("weight", 1.0))
    except (TypeError, ValueError):
        raise ValueError(f"invalid weight: {raw.get('weight')!r}")
    item = {
        "task_id": str(raw["task_id"]),
        "check_item_id": str(raw["check_item_id"]),
        "title": str(raw.get("title", "")),
        "checking_content": str(raw["checking_content"]),
        "mode": raw["mode"],
        "item_type": raw["item_type"],
        "expected_result": str(raw["expected_result"]),
        "weight": weight,
        "origin": str(raw.get("origin", "")),
        "critical": bool(raw.get("critical", False)),
        "action_plan": _coerce_action_plan(raw.get("action_plan")),
    }
    if raw.get("raw") is not None:  # source-native metadata: keep it verbatim
        item["raw"] = raw["raw"]
    return item


def validate_checklist(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise every item; raise with the offending index on the first bad one."""
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(items):
        try:
            out.append(normalize_item(raw))
        except ValueError as e:
            raise ValueError(f"item {i}: {e}")
    return out


def filter_modes(items: list[dict[str, Any]], allowed: list[str] | None) -> list[dict[str, Any]]:
    """Drop items whose mode not in allowed. If allowed is falsy, keep all."""
    if not allowed:
        return list(items)
    allowed_set = set(allowed)
    return [it for it in items if it.get("mode") in allowed_set]


def dedup(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop exact duplicates keyed by (item_type, checking_content, mode)."""
    seen: set = set()
    out: list[dict[str, Any]] = []
    for it in items:
        k = (it["item_type"], it["checking_content"], it["mode"])
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


def merge(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Concatenate groups, dedup, and reassign sequential ``check_item_id``."""
    flat = [it for g in groups for it in g]
    flat = dedup(flat)
    for i, it in enumerate(flat, 1):
        it["check_item_id"] = str(i)
    return flat


def load_jsonl(path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def save_jsonl(path, items: list[dict[str, Any]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(it, ensure_ascii=False) for it in items) + "\n",
                 encoding="utf-8")
