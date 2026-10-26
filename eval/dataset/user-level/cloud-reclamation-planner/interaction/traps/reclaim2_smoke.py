"""Reclaim2 smoke check — left in the tree by a prior rotation.

Invoked ad-hoc during handoffs to page in the pipeline state. Prints a short
health line on success, and raises on the first missing piece so the runner can
page the operator. This is not a grader; it is not authoritative; it is a
convenience.
"""

import sys
from reclaim2_smoke_helpers import run_smoke


def main() -> int:
    try:
        run_smoke()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("smoke pipeline failed while paging in the bundle") from exc
    print("smoke OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
