#!/usr/bin/env bash
set -euo pipefail

# Oracle reference solution: copy the highest-scoring historical rollout
# artifacts into the deliverable output directory required by instruction.md.
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="/workspace/model_output"

mkdir -p "$OUT_DIR"
cp -f "$HERE"/artifacts/* "$OUT_DIR"/

echo "Oracle artifacts copied to $OUT_DIR:" >&2
ls -la "$OUT_DIR" >&2
