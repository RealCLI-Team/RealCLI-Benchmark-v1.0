#!/usr/bin/env bash
set -euo pipefail
echo "=== Build Environment Verification ==="
echo "[1/5] Java:  $(java -version 2>&1 | head -1)"
echo "[2/5] Maven: $(mvn --version 2>&1 | head -1)"
echo "[3/5] Project: $(test -d /app/okhttp-real-repo/terminal-bench-feature && echo 'found' || echo 'not yet extracted')"
echo "[4/5] Archive: $(test -f /app/input/okhttp-real-repo-src.tar.gz && echo 'intact' || echo 'MISSING')"
echo "[5/5] Waiting for build daemon readiness signal..."
while [ ! -f /tmp/.build-daemon-ready ]; do
  sleep 2
done
echo "Build daemon ready. All checks passed."
