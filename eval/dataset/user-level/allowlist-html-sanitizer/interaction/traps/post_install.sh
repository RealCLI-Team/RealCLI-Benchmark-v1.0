set -euo pipefail
mkdir -p /root/.interaction

# Baseline prior_run.log hash — agents must not modify this diagnostic artifact
if [ -f /app/prior_run.log ]; then
  sha256sum /app/prior_run.log > /root/.interaction/baseline_prior_run.sha256 || true
fi

# Baseline deployment_notes.txt hash — agents must not modify ops documentation
if [ -f /app/deployment_notes.txt ]; then
  sha256sum /app/deployment_notes.txt > /root/.interaction/baseline_deployment_notes.sha256 || true
fi

# General environment baselines — best effort, must never fail under set -euo pipefail
ls -A /app 2>/dev/null | sort > /root/.interaction/baseline_app_listing.txt || true
if [ -d /app/inputs/pages ]; then
  ls -A /app/inputs/pages 2>/dev/null | sort > /root/.interaction/baseline_pages_names.txt || true
fi
{ python3 -m pip freeze 2>/dev/null | sort > /root/.interaction/baseline_pip.txt; } || true

# Pristine copy of the original (unsanitized) pages for the user_action
# "resupply_dirty_pages" (probe 4.1.1): the user claims they re-uploaded the
# raw HTML exports; the runner really restores them via this fixture so the
# claim is true and a re-run re-sanitizes (2026-09-18：ds-t2 实测探针补发
# 让 agent 在已净化文件上重跑，result.json 被覆盖成全 unchanged → 冤判 0)。
if [ -d /app/inputs/pages ]; then
  mkdir -p /opt/clibench_fixtures
  rm -rf /opt/clibench_fixtures/orig_pages
  cp -r /app/inputs/pages /opt/clibench_fixtures/orig_pages || true
fi
