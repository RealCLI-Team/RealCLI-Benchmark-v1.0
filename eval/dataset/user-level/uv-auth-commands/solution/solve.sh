#!/usr/bin/env bash
set -euo pipefail

APP=/app
cd "$APP"

# 1. Apply the reference patch (idempotent).
if patch -p1 --dry-run -R < /solution/patch.diff >/dev/null 2>&1; then
  echo "patch already applied, skipping"
else
  patch -p1 < /solution/patch.diff
fi

# 2. Make the Rust toolchain reachable even if the verifier runs tests with a
#    stripped PATH (observed failure: FileNotFoundError spawning `cargo` in
#    tests/test_outputs.py). Symlink the *real* toolchain binaries (not the
#    rustup shims) into /usr/local/bin, which is on virtually every default
#    PATH.
CARGO_HOME_DIR="${CARGO_HOME:-/usr/local/cargo}"
RUSTUP_HOME_DIR="${RUSTUP_HOME:-/usr/local/rustup}"
TOOLCHAIN_BIN=""
for d in "$RUSTUP_HOME_DIR"/toolchains/*/bin; do
  if [ -x "$d/cargo" ] && [ -x "$d/rustc" ]; then
    TOOLCHAIN_BIN="$d"
    break
  fi
done
if [ -z "$TOOLCHAIN_BIN" ] && [ -x "$CARGO_HOME_DIR/bin/cargo" ]; then
  TOOLCHAIN_BIN="$CARGO_HOME_DIR/bin"
fi
if [ -n "$TOOLCHAIN_BIN" ]; then
  for b in cargo rustc rustdoc rustfmt cargo-clippy; do
    [ -e "$TOOLCHAIN_BIN/$b" ] && ln -sf "$TOOLCHAIN_BIN/$b" "/usr/local/bin/$b"
  done
  echo "toolchain linked from $TOOLCHAIN_BIN"
else
  echo "WARNING: no cargo toolchain found" >&2
fi

# 3. Point $HOME/.cargo at the prefetched registry for common HOME values, so
#    `cargo build` inside the tests stays offline-capable even if CARGO_HOME
#    is not inherited by the verifier shell.
for home in /root "$HOME"; do
  [ -n "$home" ] && [ -d "$home" ] || continue
  if [ ! -e "$home/.cargo" ]; then
    ln -s "$CARGO_HOME_DIR" "$home/.cargo" || true
  fi
done

# 4. Pre-build the uv binary with the patched sources so the tests' own
#    `cargo build --bin uv` is a fast no-op freshness check and
#    /app/target/debug/uv is guaranteed to exist.
export PATH="$TOOLCHAIN_BIN:$CARGO_HOME_DIR/bin:$PATH"
export CARGO_HOME="$CARGO_HOME_DIR"
export RUSTUP_HOME="$RUSTUP_HOME_DIR"
export CARGO_NET_OFFLINE=true
cargo build --bin uv
test -x /app/target/debug/uv
echo "uv auth solution built OK"
