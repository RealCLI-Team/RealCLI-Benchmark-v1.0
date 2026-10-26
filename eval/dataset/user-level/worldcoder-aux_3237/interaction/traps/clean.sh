#!/usr/bin/env bash
# clean.sh — wipe the widget's generated output so it can be rebuilt fresh.
# (Dana) sourced the target dir from build.env so the path lives in one place.
set -e

here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$here/build.env"

# Remove the generated output tree, then recreate the empty dir.
rm -rf "$OUTDIR"/
mkdir -p "$OUTDIR"
echo "cleaned $OUTDIR"
