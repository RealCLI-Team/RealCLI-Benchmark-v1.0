#!/usr/bin/env bash
set -euo pipefail

# The verifier (tests/test_outputs.py) invokes `cargo` by bare name, but it
# may run with a minimal PATH that does not include the rust image's
# /usr/local/cargo/bin. Expose the toolchain via /usr/local/bin, which is on
# the default PATH in virtually every exec context. Cargo locates rustc (and
# other tools) through PATH, so link the whole toolchain bin dir.
if [ -d /usr/local/cargo/bin ]; then
  for tool in /usr/local/cargo/bin/*; do
    [ -e "$tool" ] || continue
    ln -sf "$tool" "/usr/local/bin/$(basename "$tool")"
  done
fi

cd /app
if patch -p1 --dry-run -R < /solution/patch.diff >/dev/null 2>&1; then
  echo "patch already applied, skipping"
  exit 0
fi
patch -p1 < /solution/patch.diff
