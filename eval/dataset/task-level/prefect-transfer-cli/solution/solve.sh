#!/usr/bin/env bash
set -euo pipefail
cd /app
if patch -p1 --dry-run -R < /solution/patch.diff >/dev/null 2>&1; then
  echo "patch already applied, skipping"
else
  patch -p1 < /solution/patch.diff
fi

# The verifier runs `python3 -m pytest /tests/test_outputs.py`, and the tests
# drive the `prefect` CLI via `shutil.which("prefect") or "/usr/local/bin/prefect"`.
# In the image, pytest and prefect live in /app/.venv, which is only reachable
# through the PATH set by the Dockerfile's ENV; when the harness runs test.sh
# without that PATH, `python3` resolves to /usr/local/bin/python3 (no pytest)
# and `prefect` is not found. Bridge the venv into the system interpreter so
# the tests work regardless of PATH:
VENV_SITE=$(ls -d /app/.venv/lib/python*/site-packages 2>/dev/null | head -n1)
SYS_PY=$(command -v python3 || true)
SYS_PY=${SYS_PY:-/usr/local/bin/python3}
SYS_SITE=$("$SYS_PY" -c 'import site; print(site.getsitepackages()[0])')
if [ -d "$VENV_SITE" ] && [ -d "$SYS_SITE" ]; then
  echo "$VENV_SITE" > "$SYS_SITE/prefect_venv_bridge.pth"
fi
# Make the prefect entrypoint resolvable even with a bare PATH.
if [ -x /app/.venv/bin/prefect ]; then
  ln -sf /app/.venv/bin/prefect /usr/local/bin/prefect
fi
# Sanity check: the interpreter used by test.sh must be able to import pytest.
"$SYS_PY" -c 'import pytest, prefect' && echo "verifier bridge OK"
