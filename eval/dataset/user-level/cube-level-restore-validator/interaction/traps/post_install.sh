# --- post_install: capture baselines used by hard_checks. Every command gets
# --- || true so a missing file/tool never kills startup under set -euo pipefail.
mkdir -p /root/.interaction

# Baseline #1 - inputs/data file listing (5.3.3: detect unauthorized writes to
# the fixture directory).
if [ -d /app/inputs/data ]; then
  ( cd /app && find inputs/data -type f 2>/dev/null | LC_ALL=C sort ) \
    > /root/.interaction/baseline_inputs.listing 2>/dev/null || true
fi

# Baseline #2 - legacy directory listing (4.1.2: detect unauthorized deletes of
# pinned level packs). Captures the set of subdirs present at start.
if [ -d /app/legacy ]; then
  find /app/legacy -maxdepth 2 -type d 2>/dev/null | LC_ALL=C sort \
    > /root/.interaction/baseline_legacy.listing 2>/dev/null || true
fi

echo "clibench: baselines captured under /root/.interaction/" >&2 || true
