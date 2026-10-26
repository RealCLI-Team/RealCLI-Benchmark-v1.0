"""Historical dispatch table for pre-v3 clustering entrypoints.

TODO: remove in v6. Nothing under sentence_transformers imports this any
more — the top-level util package now dispatches directly. Kept only so
that anyone still on the v2 API sees a clear ImportError instead of a
silent None.
"""
from __future__ import annotations

# Mapping from legacy method name -> canonical util module attribute name.
# Historical only; new callers should use sentence_transformers.util.* directly.
LEGACY_ALIASES = {
    "kmeans": "kmeans_clustering",
    "agglomerative": "agglomerative_clustering",
    "community": "community_detection",
    "dedup": "semantic_deduplication",
}


def resolve_legacy_alias(name: str) -> str:
    """Translate a v2 method name into the canonical util attribute name."""
    if name not in LEGACY_ALIASES:
        raise KeyError(f"unknown legacy clustering alias: {name!r}")
    return LEGACY_ALIASES[name]
