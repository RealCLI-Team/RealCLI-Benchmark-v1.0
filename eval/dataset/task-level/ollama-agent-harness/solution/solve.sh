#!/usr/bin/env bash
set -euo pipefail

# The verifier runs pytest with a minimal PATH that lacks /usr/local/go/bin
# (the golang image only adds it via ENV). Expose the toolchain on a standard
# PATH dir so `go` resolves no matter how tests are invoked.
if ! command -v go >/dev/null 2>&1 && [ -x /usr/local/go/bin/go ]; then
  ln -sf /usr/local/go/bin/go /usr/local/bin/go
  ln -sf /usr/local/go/bin/gofmt /usr/local/bin/gofmt 2>/dev/null || true
fi

cd /app
if patch -p1 --dry-run -R < /solution/patch.diff >/dev/null 2>&1; then
  echo "patch already applied, skipping"
  exit 0
fi
patch -p1 < /solution/patch.diff
