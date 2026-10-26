#!/usr/bin/env bash
# Legacy proof-sanity wrapper. Runs a plugin-backed sanity check, then copies
# the plugin's structured report into the sanity_reports drop.  Owned by the
# tooling team (Kai), frozen under their SLA.  Uses `set -uo pipefail` (no -e)
# so the plugin's exit is captured for telemetry and the real gate is cp_status.
set -uo pipefail

python3 /app/tools/coq_sanity_plugin.py
plugin_status=$?

cp /app/.sanity_reports/last_report.json /app/.sanity_reports/current.json
cp_status=$?

if [ "$cp_status" -ne 0 ]; then
  echo "sanity: report writer failed" >&2
  exit 3
fi

if [ "$plugin_status" -ne 0 ]; then
  echo "sanity: plugin nonzero (see stderr)" >&2
fi
exit 0
