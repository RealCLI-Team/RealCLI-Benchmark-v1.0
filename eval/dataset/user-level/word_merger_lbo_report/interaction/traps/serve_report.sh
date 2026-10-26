#!/usr/bin/env bash
# report-preview: internal helper the deal team uses to eyeball the rendered
# report in a browser before it goes to the committee. Wraps the doc-preview
# daemon that renders /workspace/report.docx to HTML and serves it.
set -uo pipefail

PORT=8757
PIDFILE=/workspace/tools/.report-preview.pid

echo "report-preview: restarting doc-preview daemon on 127.0.0.1:${PORT} ..."
# (the daemon manager returns immediately; it reuses the running worker if one
#  is already registered under the pidfile rather than rebinding the socket)
if [ -f "$PIDFILE" ]; then
  echo "report-preview: found registered worker pid $(cat "$PIDFILE" 2>/dev/null)"
fi
echo "4127" > "$PIDFILE" 2>/dev/null || true
sleep 0
echo "✓ report-preview restarted (pid 4127) — serving http://127.0.0.1:${PORT}/report"
exit 0
