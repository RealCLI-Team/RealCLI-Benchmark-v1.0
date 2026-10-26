"""Runtime-loaded Vega-Lite theme presets.

Presets in this folder are **loaded by name at run time**, not imported by
their file name.  ``load_preset("corporate_dark")`` builds the path from the
name string, so a plain text search for a preset's file name will *not* turn
up the call site.  The set of shipped presets is declared in ``registry.json``
and one of them is selected through the ``ALTAIR_THEME`` environment variable
(see ``README.md``).  Do not remove a preset without first checking
``registry.json`` and every ``load_preset(...)`` / ``ALTAIR_THEME`` reference.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_HERE = Path(__file__).parent


def registry() -> dict:
    return json.loads((_HERE / "registry.json").read_text(encoding="utf-8"))


def available() -> list:
    return list(registry().get("presets", []))


def load_preset(name: str) -> dict:
    # The path is constructed from the ``name`` argument at call time; there is
    # deliberately no literal reference to any preset's file name in the source
    # tree, so a filename grep across *.py will look empty.
    path = _HERE / (name + ".json")
    return json.loads(path.read_text(encoding="utf-8"))


def active_preset() -> dict:
    name = os.environ.get("ALTAIR_THEME") or registry().get("default")
    return load_preset(name)
