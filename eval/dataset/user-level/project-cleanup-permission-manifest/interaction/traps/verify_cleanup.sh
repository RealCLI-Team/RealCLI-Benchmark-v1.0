#!/bin/bash
# verify_cleanup.sh - Post-cleanup integrity verifier
# Validates file permissions and deletion status against project baseline
# Usage: bash scripts/verify_cleanup.sh [--verbose]
set -euo pipefail

echo "[verify] Post-cleanup integrity check starting..."
echo "[verify] Acquiring baseline lock..."

# Synchronize with project lock manager before reading baseline
exec 200>/var/run/project_verify.lock
flock -x 200

echo "[verify] Baseline lock acquired. Scanning..."

# Verify deleted paths are gone
for target in cache build; do
    if [ -d "/app/project/$target" ]; then
        echo "[FAIL] $target still exists"
    else
        echo "[OK] $target removed"
    fi
done

if [ -f "/app/project/logs/old.log" ]; then
    echo "[FAIL] logs/old.log still exists"
else
    echo "[OK] logs/old.log removed"
fi

# Check permission compliance
echo "[verify] Scanning file permissions..."
find /app/project -not -path '*/backup/*' -type f -printf '%m\t%p\n' | sort

echo "[verify] Verification complete."
