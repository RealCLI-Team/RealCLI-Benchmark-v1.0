#!/usr/bin/env bash
export CARGO_HOME=/usr/local/cargo RUSTUP_HOME=/usr/local/rustup PATH=/usr/local/cargo/bin:$PATH

set -uo pipefail

# CliBench: collect interaction evidence on every exit path, after the
# original verifier has already written reward. Never changes the verdict.
trap 'if [ -f /tests/collect_evidence.sh ]; then bash /tests/collect_evidence.sh || true; fi' EXIT

# --- Defensive disk cleanup ------------------------------------------------
# This task's agent drives multi-round `cargo build/check --workspace` in
# /app; the Rust target/ tree can grow past 15 GB and fill the 30 GB sandbox
# disk. A full disk previously killed the verifier phase with HTTP 507
# (0 bytes free on /tmp) before any reward could be downloaded. Reclaim
# space around the test run. Only build artifacts under $APP_DIR/target are
# ever removed — never sources, never the vendored cargo registry (needed
# for the offline rebuild in test_outputs.py), never anything outside
# $APP_DIR/target. df is logged before and after each stage.
APP_DIR="${UV_AUTH_VERIFY_APP_DIR:-/app}"
LOG_DIR="${UV_AUTH_VERIFY_LOG_DIR:-/logs/verifier}"
CLEANUP_LOG="$LOG_DIR/disk_cleanup.log"
MIN_FREE_KB=2097152   # 2 GB headroom for harbor uploads, logs and evidence

mkdir -p "$LOG_DIR"

disk_note() {
  { echo "== df $1 =="; df -h "$APP_DIR" /tmp 2>/dev/null || df -h; } \
    | tee -a "$CLEANUP_LOG"
}

safe_rm_rf() {
  # rm -rf $1 only when it is an existing directory at an expected build-
  # artifact location strictly under $APP_DIR/target.
  case "$1" in
    "$APP_DIR"/target|"$APP_DIR"/target/*/incremental) ;;
    *) echo "disk-cleanup: refusing unexpected path: $1" | tee -a "$CLEANUP_LOG"; return 1 ;;
  esac
  if [ -d "$1" ]; then
    echo "disk-cleanup: rm -rf $1 ($(du -sh "$1" 2>/dev/null | cut -f1))" | tee -a "$CLEANUP_LOG"
    rm -rf -- "$1"
  fi
}

free_kb() {
  df -k --output=avail "$APP_DIR" 2>/dev/null | tail -n 1 | tr -d ' '
}

# Stage 1 (pre-test): drop pure rebuild caches. The session-scoped
# `cargo build --bin uv` in test_outputs.py reuses target/debug/deps and
# .fingerprint, so it stays incremental without the incremental/ caches;
# removing them frees several GB while keeping the verifier build fast.
disk_note "before pre-test cleanup"
for d in "$APP_DIR"/target/*/incremental; do
  [ -d "$d" ] && safe_rm_rf "$d"
done
avail="$(free_kb)"
if [ -n "$avail" ] && [ "$avail" -lt "$MIN_FREE_KB" ] && [ -d "$APP_DIR/target" ]; then
  echo "disk-cleanup: only ${avail} KB free after cache cleanup; wiping $APP_DIR/target (verifier rebuilds from vendored deps)" | tee -a "$CLEANUP_LOG"
  safe_rm_rf "$APP_DIR/target"
fi
disk_note "after pre-test cleanup"
# --- End defensive disk cleanup --------------------------------------------

python3 -m pytest /tests/test_outputs.py -v
status=$?
if [ "$status" -eq 0 ]; then
  echo 1 > "$LOG_DIR/reward.txt"
else
  echo 0 > "$LOG_DIR/reward.txt"
fi

# Stage 2 (post-test, pre-evidence): the behavior tests are done, so the
# whole target/ tree is dead weight. Remove it before the EXIT trap runs
# collect_evidence.sh, which copies /app into $LOG_DIR — with target/ still
# present that copy would refill the disk and bloat the harbor download.
# Nothing in the verifier, hard_checks or the judge depends on target/
# after this point.
disk_note "before post-test cleanup"
safe_rm_rf "$APP_DIR/target"
disk_note "after post-test cleanup"

exit "$status"
