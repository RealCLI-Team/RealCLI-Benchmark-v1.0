#!/bin/bash
set -euo pipefail

mkdir -p /logs/verifier

# The golden expected report is not shipped in the agent workspace (answer-leak
# guard). Inject the verifier's own copy into the unpacked project so that the
# repo's MaintenanceAuditorTest fixture assertion runs during `mvn test`.
FIXTURE=/app/okhttp-real-repo/tools/maintenance-audit/src/test/resources/expected-maintenance-report.json
if [ -d /app/okhttp-real-repo/tools/maintenance-audit ]; then
  mkdir -p "$(dirname "$FIXTURE")"
  cp /tests/expected-maintenance-report.json "$FIXTURE"
fi

if pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
  exit 1
fi
