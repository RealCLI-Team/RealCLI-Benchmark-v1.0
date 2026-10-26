mkdir -p /root/.interaction
# environment_mode = "separate": /root/.interaction does NOT propagate to the
# verifier container. Hard_checks use inline content canaries on files that
# live inside /The-Spirit (which is copied over as the artifact). Still leave
# a sanity listing here so the interaction snapshot has *some* view of the
# agent-side state, in case debugging needs it.
ls -A /The-Spirit 2>/dev/null | sort > /root/.interaction/baseline_the_spirit_listing.txt || true
{ command -v node && node --version; } > /root/.interaction/baseline_node.txt 2>&1 || true
