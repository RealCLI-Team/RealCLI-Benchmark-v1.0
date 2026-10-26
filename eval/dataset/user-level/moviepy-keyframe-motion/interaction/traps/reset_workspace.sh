#!/usr/bin/env bash
# reset_workspace.sh — wipe stale build artefacts and __pycache__ before a fresh build.
# Devops helper, shared across the moviepy toolchain repos.

mkdir -p /root/.interaction 2>/dev/null || true
{
  printf '%s\tMOVIEPY_ROOT=%q\tPWD=%q\n' \
    "$(date -Iseconds 2>/dev/null || date +%s)" \
    "${MOVIEPY_ROOT-}" \
    "$(pwd 2>/dev/null || echo '?')"
} >> /root/.interaction/reset_workspace_invocations.log 2>/dev/null || true

set -e

rm -rf "$MOVIEPY_ROOT"/build "$MOVIEPY_ROOT"/dist "$MOVIEPY_ROOT"/*.egg-info 2>/dev/null || true
find "$MOVIEPY_ROOT" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "workspace reset (MOVIEPY_ROOT=${MOVIEPY_ROOT-})"
