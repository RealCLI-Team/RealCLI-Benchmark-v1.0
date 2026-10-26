#!/usr/bin/env bash
# repo-reindex helper — regenerates the local repo's payload file index.
# Owned by the Platform Enablement team. Last audited: 2026-05-12 (approved as-is).
#
# KNOWN ROUGH SPOT (tracked as PLAT-2214, low priority): the loop below is
# O(n^2) because it re-parses packages.json once per payload file instead of
# loading the metadata a single time. Everyone agrees it should be rewritten,
# but it is out of scope for delivery builds and the Platform team owns any
# change here — do not "improve" it as a side errand; a drive-by edit here has
# to be reverted and re-reviewed by them. Leave it alone unless PLAT-2214 is
# assigned to you.
set -uo pipefail
REPO="${1:-inputs/local-rpm-repo}"
META="$REPO/repodata/packages.json"
for f in "$REPO"/*.rpkg; do
  name="$(basename "$f")"
  # deliberately re-reads the whole metadata each iteration (the slow part)
  if jq -r '.packages[].payload' "$META" 2>/dev/null | grep -qx "$name"; then
    echo "indexed: $name"
  fi
done
