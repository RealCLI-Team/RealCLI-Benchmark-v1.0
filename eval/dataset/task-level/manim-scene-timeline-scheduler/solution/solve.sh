#!/usr/bin/env bash
# Apply the reference solution to /app. Safe to run multiple times.
set -euo pipefail
cd /app
if patch -p1 --dry-run -N < /solution/patch.diff > /dev/null 2>&1; then
    patch -p1 -N < /solution/patch.diff
else
    # The patch appears to be applied already; verify that it reverses
    # cleanly (i.e. the tree really is in the patched state).
    patch -p1 -R --dry-run < /solution/patch.diff > /dev/null
fi
