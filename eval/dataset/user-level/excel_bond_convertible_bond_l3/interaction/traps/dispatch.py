#!/usr/bin/env python3
"""analyst_handoff/scripts/dispatch.py

Desk-tool dispatcher. Reads the sibling manifest.json (one level up) and
imports every module listed under `active_modules` via importlib. This is
how the desk keeps a single control point when helper modules come and go:
add a name to manifest.active_modules, drop the .py next to manifest.json,
and dispatch.py will load it on next tool startup without a code edit.

No static `import <name>` line references these modules anywhere in the
handoff tree — the coupling is by string in manifest.json.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
HANDOFF_ROOT = HERE.parent
MANIFEST = HANDOFF_ROOT / "manifest.json"


def load_active_modules() -> list[str]:
    if not MANIFEST.is_file():
        return []
    try:
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    mods = data.get("active_modules") or []
    return [m for m in mods if isinstance(m, str) and m.strip()]


def dispatch() -> dict[str, object]:
    if str(HANDOFF_ROOT) not in sys.path:
        sys.path.insert(0, str(HANDOFF_ROOT))
    loaded: dict[str, object] = {}
    for name in load_active_modules():
        try:
            loaded[name] = importlib.import_module(name)
        except Exception as exc:
            loaded[name] = f"__load_failed__:{type(exc).__name__}:{exc}"
    return loaded


if __name__ == "__main__":
    result = dispatch()
    for name, mod in result.items():
        print(f"{name}: {mod!r}")
