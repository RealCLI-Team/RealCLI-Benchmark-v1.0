# TGI scheduler-v3 task environment.
# Base snapshot: huggingface/text-generation-inference @ fec0167a
# (rust 1.78.0 per repository/rust-toolchain.toml).
FROM rust:1.78.0-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        patch \
        pkg-config \
        libssl-dev \
        protobuf-compiler \
        procps \
        python3 \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

# pytest plus the gRPC tooling the test harness uses to stand up mock
# model shards (generate.v2 / generate.v3) over a unix socket.
# huggingface_hub 用于构建期预取 gpt2 tokenizer（见下方 HF 缓存暖仓）。
RUN python3 -m pip install --break-system-packages --no-cache-dir \
        pytest \
        grpcio \
        grpcio-tools \
        huggingface_hub

# Components referenced by repository/rust-toolchain.toml; use the git CLI
# for the git-sourced crates (e.g. minijinja).
RUN rustup component add rustfmt clippy
ENV CARGO_NET_GIT_FETCH_WITH_CLI=true \
    CARGO_INCREMENTAL=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Source repository: https://github.com/huggingface/text-generation-inference.git @ fec0167a123eddc60891320dd263e974671ad1c9
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git init -q /app \
    && cd /app \
    && git remote add origin https://github.com/huggingface/text-generation-inference.git \
    && git fetch -q --depth 1 origin fec0167a123eddc60891320dd263e974671ad1c9 \
    && git checkout -q FETCH_HEAD
WORKDIR /app

# Warm the cargo registry/git caches and the target dir so that, once the
# solution is applied, incremental rebuilds (including the test profile of
# the workspace's own unit tests) stay well inside the verifier budget.
RUN cargo fetch \
    && cargo build --workspace \
    && cargo test --workspace --no-run

# 参考解 patch 把 10 个锁定 crate 升了小版本并给 client 加 async-trait；原暖仓只有
# 旧版本，离线构建解析 patched lockfile 会缺新版本。用 patched Cargo.lock（仅锁文件，
# 不含任何 solution 代码）在仓库副本上 cargo fetch --locked 预取，uv 题同款暖仓模式。
COPY warm-lock.toml /tmp/warm/Cargo.lock
RUN cp -r /app /tmp/warm/ws && cp /tmp/warm/Cargo.lock /tmp/warm/ws/Cargo.lock \
    && sed -i 's/^\[dependencies\]$/[dependencies]\nasync-trait = "^0.1"/' /tmp/warm/ws/router/client/Cargo.toml \
    && cd /tmp/warm/ws && cargo fetch --locked && cd / && rm -rf /tmp/warm

# 评分期 --network=none：router 的 validation 单测会从 huggingface.co 在线拉取
# gpt2 的 tokenizer.json（tokenizers from_pretrained("gpt2")）。构建期（有网）预取进
# HF 缓存，运行时 hf-hub 走离线模式直接命中缓存。
RUN python3 -c "from huggingface_hub import snapshot_download; \
    snapshot_download('gpt2', allow_patterns=['tokenizer.json'])"
ENV HF_HUB_OFFLINE=1

# 评分期可能 --network=none：全部暖仓完成后禁止 cargo 再访问网络索引（否则
# verifier 内 cargo build 会先尝试更新 crates.io index 并失败）。必须放在所有
# fetch 之后，否则构建期自己也被断网。
ENV CARGO_NET_OFFLINE=true

CMD ["bash"]

# Sandbox exec drops image ENV (CARGO_HOME/RUSTUP_HOME/PATH); re-expose the
# rust toolchain via PATH-independent wrappers under /usr/local/bin.
RUN set -e; CH=$( [ -d /usr/local/cargo ] && echo /usr/local/cargo || echo $HOME/.cargo ); \
    RH=$( [ -d /usr/local/rustup ] && echo /usr/local/rustup || echo $HOME/.rustup ); \
    for b in cargo rustc rustup cargo-clippy cargo-fmt; do \
      src="$(ls $CH/bin/$b /root/.cargo/bin/$b /usr/local/cargo/bin/$b 2>/dev/null | head -1)"; \
      [ -n "$src" ] && printf '#!/bin/sh\nexport CARGO_HOME=%s RUSTUP_HOME=%s\nexec %s "$@"\n' "$CH" "$RH" "$src" > /usr/local/bin/$b && chmod +x /usr/local/bin/$b; \
    done; command -v cargo

