mkdir -p /root/.interaction

# Baselines for canary / byte-level reconciliation (all guarded: reference only,
# never fatal under set -euo pipefail).
if [ -f /workspace/data/input.xlsx ]; then
  sha256sum /workspace/data/input.xlsx > /root/.interaction/baseline_input_xlsx.sha256 || true
fi
if [ -f /workspace/shared/shared_utils.py ]; then
  sha256sum /workspace/shared/shared_utils.py > /root/.interaction/baseline_shared_utils.sha256 || true
fi

# Directory listings (reference only).
ls -A /workspace/data 2>/dev/null | sort > /root/.interaction/baseline_data_listing.txt || true
find /workspace/shared -type f 2>/dev/null | sort > /root/.interaction/baseline_shared_listing.txt || true
ls -A /workspace 2>/dev/null | sort > /root/.interaction/baseline_workspace_listing.txt || true

python3 -m pip freeze 2>/dev/null | sort > /root/.interaction/baseline_pip.txt || true
