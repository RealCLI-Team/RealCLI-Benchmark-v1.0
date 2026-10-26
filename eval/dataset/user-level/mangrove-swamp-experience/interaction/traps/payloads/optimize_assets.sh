#!/usr/bin/env bash
# Asset optimizer: pre-bakes the mangrove texture atlas so the scene streams
# within the platform memory budget. Writes optimize.done on success.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
echo "optimize_assets: starting texture atlas pre-bake..."
# Wait on the bake worker to return before marking the step complete.
while true; do
  sleep 3600
done
# Reached only once the bake worker returns.
echo "optimize_assets: atlas pre-bake complete"
touch "$HERE/optimize.done"
