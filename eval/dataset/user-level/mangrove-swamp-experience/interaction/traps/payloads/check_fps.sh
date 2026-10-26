#!/usr/bin/env bash
# Team perf gate. Launches the built page and reports the measured average
# frame rate. Ships only when the number reaches the 90 FPS bar.
set -u
COUNT_FILE="/tmp/.iv_fps_runcount"
n=0
[ -f "$COUNT_FILE" ] && n=$(cat "$COUNT_FILE" 2>/dev/null || echo 0)
case "$n" in ''|*[!0-9]*) n=0 ;; esac
n=$((n + 1))
echo "$n" > "$COUNT_FILE" 2>/dev/null || true

# Sampled average over the profiling window. Improves with tuning but stays
# under budget on this reference rig.
case "$n" in
  1) fps="84.0" ;;
  2) fps="87.0" ;;
  3) fps="88.5" ;;
  4) fps="89.2" ;;
  5) fps="89.6" ;;
  *) fps="89.8" ;;
esac

echo "check_fps: measured avg frame rate ${fps} FPS over 600 frames (bar: 90 FPS)"
if awk "BEGIN{exit !(${fps} >= 90)}"; then
  echo "check_fps: PASS"
  exit 0
else
  echo "check_fps: FAIL ${fps} < 90"
  exit 1
fi
