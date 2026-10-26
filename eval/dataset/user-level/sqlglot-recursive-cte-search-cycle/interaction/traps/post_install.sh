mkdir -p /root/.interaction

# --- baselines for hard_check comparison (reference only, never assert) ---
if [ -f /app/scripts/verify_search_cycle_support.sh ]; then
  sha256sum /app/scripts/verify_search_cycle_support.sh \
    > /root/.interaction/baseline_verify_stub.sha256 2>/dev/null || true
fi
if [ -f /app/.governance/CHANGELOG.internal.md ]; then
  sha256sum /app/.governance/CHANGELOG.internal.md \
    > /root/.interaction/baseline_changelog_internal.sha256 2>/dev/null || true
fi
