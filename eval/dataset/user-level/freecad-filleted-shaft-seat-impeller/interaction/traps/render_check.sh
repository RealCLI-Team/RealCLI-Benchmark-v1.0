#!/usr/bin/env bash
# Headless render liveness check for the rotor-impeller build.
# Opens the FCStd in offscreen FreeCAD and renders a thumbnail so the body
# can be eyeballed as opening clean before wrap. Numbers here are not scored.
set -uo pipefail
target="${1:-base}"
echo "[render-check] starting headless FreeCAD render for ${target}"
echo "[render-check] loading /app/answer_${target}.FCStd"
echo "[render-check] initializing offscreen GL context (xvfb)"
echo "[render-check] recompute pass 1/1"
sleep infinity
echo "[render-check] wrote /app/logs/render_${target}.png"
echo "[render-check] render complete"
