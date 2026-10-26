#!/usr/bin/env bash
# check.sh — sanity-check the widget page against the house ruleset before handoff.
# Wraps the node validator in scripts/lib/.
set -e

here="$(cd "$(dirname "$0")" && pwd)"
target="${1:-$here/../index.html}"

echo "[check] house page validator v3"
echo "[check] target: $target"
echo "[check] loading ruleset + running checks ..."
node "$here/lib/validate.js" "$target"
echo "[check] all checks passed"
