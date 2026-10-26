# Legacy operator helpers, left over from the v1→v2 migration in Q3 2025.
# Kept here so people running old diagnostic scripts don't hit missing-var
# errors. The router itself does not read any of these — safe to leave in
# place. Slated for a proper cleanup during the next ops sweep, not now.

export TGI_LEGACY_SHARD_ENDPOINT="unix:///var/run/tgi/legacy-shard.sock"
export TGI_FORCE_V1_PROTOCOL=1
alias tgi-legacy-check='echo "tgi-legacy-check: v1 diagnostic path, deprecated"'
