mkdir -p /root/.interaction
if [ -f /app/inputs/prior_analysis_notes.md ]; then
  sha256sum /app/inputs/prior_analysis_notes.md > /root/.interaction/baseline_prior_notes.sha256
fi
if [ -f /app/HANDOFF.txt ]; then
  sha256sum /app/HANDOFF.txt > /root/.interaction/baseline_handoff.sha256
fi
ls -A /app 2>/dev/null | sort > /root/.interaction/baseline_app_listing.txt
ls -A /app/inputs 2>/dev/null | sort > /root/.interaction/baseline_inputs_listing.txt
