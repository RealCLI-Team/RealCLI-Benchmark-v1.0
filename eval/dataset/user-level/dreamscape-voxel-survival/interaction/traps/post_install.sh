mkdir -p /root/.interaction
# environment_mode = "separate": /root/.interaction does NOT propagate to
# the verifier container. Hard_checks use inline content canaries on files
# that live inside /dreamscape-voxel-survival_0132 (which the verifier copies
# as the artifact). Keep a sanity listing here for interaction-side debug.
ls -A /dreamscape-voxel-survival_0132 2>/dev/null | sort > /root/.interaction/baseline_workspace_listing.txt || true
{ command -v node && node --version; } > /root/.interaction/baseline_node.txt 2>&1 || true
