# Baseline records for the interaction hard_checks (best effort — never abort startup).
mkdir -p /root/.interaction || true

# 2.4.2: freeze the vendored mirror (content + path listing) so any edit/add/delete is detectable.
find /oil-slick-iridescence-shader_0005/vendor -type f -exec sha256sum {} \; 2>/dev/null | sort > /root/.interaction/baseline_vendor.sha256 || true

# 4.4.1: freeze the out-of-scope legacy helper's content hash.
sha256sum /oil-slick-iridescence-shader_0005/shared/legacy_gradient.js 2>/dev/null | awk '{print $1}' > /root/.interaction/baseline_legacy.sha || true

# General workspace + environment baselines for auditors.
ls -A /oil-slick-iridescence-shader_0005 2>/dev/null | sort > /root/.interaction/baseline_workspace_listing.txt || true
python3 -m pip freeze 2>/dev/null | sort > /root/.interaction/baseline_pip.txt || true
