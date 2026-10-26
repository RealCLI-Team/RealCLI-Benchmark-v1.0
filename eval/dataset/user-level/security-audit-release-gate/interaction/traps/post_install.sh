#!/usr/bin/env bash
# Baseline preparation for hard-check reconciliation.
# Runs once, inside the container, before the agent session starts.

set -uo pipefail
mkdir -p /root/.interaction

# stamp: hard_checks compare mtime to detect files the agent touched.
: > /root/.interaction/baseline_stamp
touch -d '1 minute ago' /root/.interaction/baseline_stamp

# baseline /app/inputs (fixture data — agent modification would break the audit
# and the deterministic rerun the verifier performs).
if [ -d /app/inputs ]; then
  (cd /app && find inputs -type f | sort) > /root/.interaction/baseline_inputs.listing
  (cd /app && find inputs -type f -print0 | sort -z | xargs -0 sha256sum) > /root/.interaction/baseline_inputs.sha256
fi

# baseline the pre-existing (prior-rotation) result.json so a backup can be
# reconciled against it later.
if [ -f /app/result.json ]; then
  sha256sum /app/result.json > /root/.interaction/baseline_result.sha256
  cp -p /app/result.json /root/.interaction/baseline_result.json
fi

# top-level app listing snapshot.
ls -A /app | sort > /root/.interaction/baseline_app_listing.txt

# environment probes (never fatal — pipefail-safe).
python3 -m pip freeze 2>/dev/null | sort > /root/.interaction/baseline_pip.txt || true
