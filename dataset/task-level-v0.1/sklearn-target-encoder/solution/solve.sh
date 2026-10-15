#!/usr/bin/env bash
set -euo pipefail
cd /app
if patch -p1 --dry-run -s < /solution/patch.diff; then
  patch -p1 -s < /solution/patch.diff
fi
# Rebuild C extensions so the Cython module added by the patch is compiled.
python setup.py build_ext --inplace -j 4
