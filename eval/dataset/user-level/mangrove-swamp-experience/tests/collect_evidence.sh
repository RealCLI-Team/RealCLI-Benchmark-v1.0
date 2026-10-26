#!/usr/bin/env bash
# CliBench evidence collector. Never asserts, never affects reward.
set -uo pipefail

EVID=/logs/verifier/interaction_evidence
mkdir -p "$EVID/snapshot/root_interaction"

find /mangrove-swamp-experience_0110 \( -type f -o -type d -o -type l \) | sort | while read -r p; do
  type=$(stat -c '%F' "$p" 2>/dev/null || echo '?')
  mode=$(stat -c '%a' "$p" 2>/dev/null || echo '?')
  if [ -f "$p" ]; then
    hash=$(sha256sum "$p" 2>/dev/null | cut -d' ' -f1)
  else
    hash='-'
  fi
  printf '%s\t%s\t%s\t%s\n' "$type" "$mode" "$hash" "$p"
done > "$EVID/snapshot/app_listing.tsv" 2>/dev/null

df -h > "$EVID/snapshot/df.txt" 2>&1
{ command -v jq && echo "jq PRESENT"; } > "$EVID/snapshot/jq_check.txt" 2>&1 || echo "jq MISSING" >> "$EVID/snapshot/jq_check.txt"
python3 -m pip freeze 2>/dev/null | sort > "$EVID/snapshot/pip_freeze.txt"
cp -r /mangrove-swamp-experience_0110/. "$EVID/snapshot/app/" 2>/dev/null || true
cp -r /root/.interaction/* "$EVID/snapshot/root_interaction/" 2>/dev/null || true

if command -v python3 >/dev/null 2>&1; then
  python3 /tests/run_hard_checks.py /tests/evidence_checks.json "$EVID/hard_checks.json"
elif command -v perl >/dev/null 2>&1 && [ -f /tests/run_hard_checks.pl ]; then
  perl /tests/run_hard_checks.pl /tests/evidence_checks.json "$EVID/hard_checks.json"
fi
echo "evidence collected to $EVID"
