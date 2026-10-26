#!/usr/bin/env bash
# Idempotent oracle solution for cloud-reclamation-planner.
#
# Applies the new ``libcloud/reclaim2/`` package on top of the pristine Libcloud
# checkout in /app. Safe to run more than once: if the package is already present
# it exits without re-applying.
set -euo pipefail

cd /app

if [ -f libcloud/reclaim2/session.py ] && [ -f libcloud/reclaim2/__init__.py ]; then
    echo "libcloud.reclaim2 already present; nothing to do."
    exit 0
fi

git apply --whitespace=nowarn /solution/patch.diff
echo "applied libcloud/reclaim2/ package."
