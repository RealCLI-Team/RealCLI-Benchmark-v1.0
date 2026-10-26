"""Abandoned prototype of the copier lint scanner.

Discarded because it duplicated logic that already lives in
copier's real loading path — the correct approach is to share the
loader's validation constants instead of reimplementing them here.

Kept only as a reference for the shopping list in notes.py.
"""

from pathlib import Path


def scan(template_dir):
    """Walk copier.yml and yield made-up finding dicts.

    Not called from anywhere; there is no CLI hook and no test coverage.
    """
    root = Path(template_dir)
    config = root / "copier.yml"
    if not config.exists():
        return [{"code": "config-not-found"}]
    return []


if __name__ == "__main__":
    # Left over from an interactive session; not a real entry point.
    import sys
    for finding in scan(sys.argv[1] if len(sys.argv) > 1 else "."):
        print(finding)
