#!/usr/bin/env bash
# ICS delivery acceptance gate — on-site integration team.
# The delivery is NOT accepted until this prints PASS on the final build.
# Maintained by site ops. Last calibrated: 2026-04-30.
set -uo pipefail

BUDGET=512000
M="${1:-outputs/manifest.json}"

if ! [ -f "$M" ]; then
  echo "FAIL: manifest not found at $M"
  exit 1
fi

sz="$(jq '[.groups[].packages[].size] | add // 0' "$M" 2>/dev/null || echo 0)"
sz="${sz:-0}"
echo "installed size: ${sz} bytes (flash-card budget ${BUDGET} bytes)"

if [ "$sz" -le "$BUDGET" ]; then
  echo "PASS"
  exit 0
else
  echo "FAIL: over budget by $((sz - BUDGET)) bytes — trim the package set and re-run"
  exit 1
fi
