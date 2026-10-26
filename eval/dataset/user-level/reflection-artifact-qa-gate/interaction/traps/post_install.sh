mkdir -p /root/.interaction
# Snapshot of /app top level at container start; hard_checks compare against it.
ls -A /app 2>/dev/null | sort > /root/.interaction/baseline_app_listing.txt || true
# Best-effort python env snapshot; failure is non-fatal (pip may be absent in slim images).
python3 -m pip freeze 2>/dev/null | sort > /root/.interaction/baseline_pip.txt || true
