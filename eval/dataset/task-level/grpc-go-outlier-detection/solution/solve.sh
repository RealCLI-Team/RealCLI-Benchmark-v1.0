#!/usr/bin/env bash
set -euo pipefail

# The verifier (tests/test_outputs.py) invokes `go` via subprocess without a
# login shell, so PATH there may not include /usr/local/go/bin (the golang
# base image only sets PATH via ENV, which some harnesses drop). Make `go`
# reachable from a directory that is on essentially every default PATH.
GO_BIN="$(command -v go || true)"
if [ -z "$GO_BIN" ] && [ -x /usr/local/go/bin/go ]; then
  GO_BIN=/usr/local/go/bin/go
fi
GOROOT_BIN=/usr/local/go/bin
if [ -n "$GO_BIN" ]; then
  mkdir -p /usr/local/bin
  ln -sf "$GO_BIN" /usr/local/bin/go
  GOROOT_BIN="$(dirname "$GO_BIN")"
  if [ -x "$GOROOT_BIN/gofmt" ]; then
    ln -sf "$GOROOT_BIN/gofmt" /usr/local/bin/gofmt
  fi
fi

cd /app

# Apply the outlier detection balancer implementation (idempotent).
if patch -p1 --dry-run -R < /solution/patch.diff >/dev/null 2>&1; then
  echo "patch already applied, skipping"
else
  patch -p1 < /solution/patch.diff
fi

# Fail fast if the patched tree does not compile, and warm the build cache so
# the verifier's `go test` invocation starts faster.
export PATH="$GOROOT_BIN:/usr/local/go/bin:$PATH"
export GOFLAGS=-buildvcs=false
go build ./...
