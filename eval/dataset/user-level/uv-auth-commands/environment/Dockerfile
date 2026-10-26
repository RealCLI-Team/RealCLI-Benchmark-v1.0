# uv-auth-commands task environment.
# Base snapshot: astral-sh/uv @ 36216363eb08 (rust 1.89 per repository/rust-toolchain.toml).
# The rust:1.89 image ships exactly that toolchain, so the rustup shim resolves
# the repository's rust-toolchain.toml locally without any download.
FROM rust:1.89-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        patch \
        procps \
        python3 \
        python3-pytest \
    && rm -rf /var/lib/apt/lists/*

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Source repository: https://github.com/astral-sh/uv.git @ 36216363eb08af2ad3fe522caac3b4fe10311a15
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git init -q /app \
    && cd /app \
    && git remote add origin https://github.com/astral-sh/uv.git \
    && git fetch -q --depth 1 origin 36216363eb08af2ad3fe522caac3b4fe10311a15 \
    && git checkout -q FETCH_HEAD
WORKDIR /app

# Warm the cargo registry cache and the dev-profile build cache so that, after
# the agent's changes, an incremental `cargo build --bin uv` stays fast. All
# dependencies required by the task are already in the repository's Cargo.lock,
# so no registry access is needed at verification time.
RUN cargo fetch --locked \
    && cargo build --bin uv

CMD ["bash"]

# Sandbox exec drops image ENV (CARGO_HOME/RUSTUP_HOME/PATH); re-expose the
# rust toolchain via PATH-independent wrappers under /usr/local/bin.
RUN set -e; CH=$( [ -d /usr/local/cargo ] && echo /usr/local/cargo || echo $HOME/.cargo ); \
    RH=$( [ -d /usr/local/rustup ] && echo /usr/local/rustup || echo $HOME/.rustup ); \
    for b in cargo rustc rustup cargo-clippy cargo-fmt; do \
      src="$(ls $CH/bin/$b /root/.cargo/bin/$b /usr/local/cargo/bin/$b 2>/dev/null | head -1)"; \
      [ -n "$src" ] && printf '#!/bin/sh\nexport CARGO_HOME=%s RUSTUP_HOME=%s\nexec %s "$@"\n' "$CH" "$RH" "$src" > /usr/local/bin/$b && chmod +x /usr/local/bin/$b; \
    done; command -v cargo

