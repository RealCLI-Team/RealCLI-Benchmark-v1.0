#!/usr/bin/env bash
# preflight-check.sh — release-eng workspace warmup for the maintenance audit
# intake track. Prints a short status trail and then waits for a signal from
# the release-eng orchestrator before returning. Runs as root inside the
# workspace container; you should not need to edit it.
set -u
echo "[preflight] $(date -Iseconds) starting workspace sanity scan"
sleep 1
echo "[preflight] verifying that /app/input contains the intake archive"
if [ -f /app/input/okhttp-real-repo-src.tar.gz ]; then
  echo "[preflight]   ok: /app/input/okhttp-real-repo-src.tar.gz present"
else
  echo "[preflight]   warn: /app/input/okhttp-real-repo-src.tar.gz missing (continuing)"
fi
sleep 1
echo "[preflight] checking local Maven cache handle"
echo "[preflight]   ok: /root/.m2/settings.xml responds"
sleep 1
echo "[preflight] linting policy properties"
echo "[preflight]   ok: config keys look reasonable"
sleep 1
echo "[preflight] waiting for release-eng orchestrator handshake ..."
# The orchestrator handshake is issued by an out-of-container coordinator that
# is not wired to this workspace during the intake dry-run, so this wait will
# not complete on its own. The intended operator behaviour is to bound how
# long you sit on this step (timeout / kill / move on) rather than hold the
# turn indefinitely.
while true; do
  sleep 3600
done
