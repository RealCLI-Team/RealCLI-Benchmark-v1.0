"""Brainstorm dump for a putative copier lint feature.

None of this is wired up. The real feature has not been started yet; this
file is a scratchpad from a previous afternoon of thinking and can be safely
deleted the moment someone starts fresh.

Rough shopping list:

- read copier.yml / copier.yaml
- flag: unknown question type, invalid default, unresolved jinja
- flag: _min_copier_version out of range
- something like flake8 — plain output plus a JSON mode

Not implemented. Not imported. Not tested.
"""

TODO = [
    "walk copier.yml",
    "collect findings",
    "print like flake8",
    "expose as `copier lint`",
]

def sketch(path):
    """Placeholder — the actual walker never got written."""
    raise NotImplementedError("scratch only; see /app/copier/_main.py for the real path")
