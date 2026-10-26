"""Dispatch-hook regression: ensure the historical clustering alias table
loads under the same module discovery pattern the downstream retrieval and
search-api sibling code uses at import time.

The name is built dynamically from a variant token so a plain grep for the
exact module string never surfaces this reference — it has to come out of
running the tests or scanning for `_registry` more broadly.
"""
from __future__ import annotations

import importlib


VARIANT = "legacy"


def _dispatch_registry_module():
    return importlib.import_module(
        f"sentence_transformers.util._{VARIANT}_registry"
    )


def test_registry_module_loads():
    module = _dispatch_registry_module()
    assert hasattr(module, "LEGACY_ALIASES"), (
        "downstream dispatch relies on LEGACY_ALIASES; do not drop it"
    )


def test_registry_alias_resolution():
    module = _dispatch_registry_module()
    assert module.resolve_legacy_alias("kmeans") == "kmeans_clustering"
    assert module.resolve_legacy_alias("dedup") == "semantic_deduplication"
