"""Second-hop helpers for the reclaim2 smoke.

Kept out of libcloud.reclaim2 so the smoke can run against arbitrary reclaim2
implementations without polluting their namespace. The heavy lifting sits in
reclaim2_smoke_bundle; this module is just the dispatch shim.
"""

from reclaim2_smoke_bundle import verify_bundle


def run_smoke() -> None:
    verify_bundle()
