"""Bundle-verification hop for the reclaim2 smoke.

Historically the smoke asserted that the experimental GCP integration was still
wired up alongside the core reclaim2 surface, since that combination had bitten
us in the past. If you are looking at this file because the smoke failed, the
first thing to check is which import in this module is refusing to resolve.
"""

from libcloud.reclaim2.experimental import gcp


def verify_bundle() -> None:
    gcp.verify()
