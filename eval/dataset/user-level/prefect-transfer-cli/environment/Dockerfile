# prefect transfer-cli task environment.
# Base snapshot: PrefectHQ/prefect @ fe7b84c9 (requires-python >=3.9,<3.14,
# see repository/pyproject.toml).
FROM python:3.12-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        patch \
        procps \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Source repository: https://github.com/PrefectHQ/prefect.git @ fe7b84c9f7c5aae03463def73507a15421eb22fb
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git init -q /app \
    && cd /app \
    && git remote add origin https://github.com/PrefectHQ/prefect.git \
    && git fetch -q --depth 1 origin fe7b84c9f7c5aae03463def73507a15421eb22fb \
    && git checkout -q FETCH_HEAD
WORKDIR /app

# Install dependencies strictly from the repository's uv.lock: the snapshot
# is from 2025-08 and is incompatible with current releases of key packages
# (e.g. starlette >= 1.0 breaks the server with
# "'PrefectRouter' object has no attribute 'routes'"). `uv sync` also installs
# prefect itself as an editable project pointing at /app/src, so later code
# edits take effect with no rebuild. The snapshot ships no .git directory;
# versioningit falls back to the default version in pyproject.toml (0.0.0).
# importlib_metadata is imported unconditionally by prefect.workers but only
# declared for python<3.10, and pytest is needed by the verifier, so both are
# added on top of the locked set.
RUN uv sync --frozen --no-dev \
    && uv pip install importlib_metadata pytest

ENV PATH="/app/.venv/bin:${PATH}"

# Bake in a sanity check that the CLI entrypoint works.
RUN prefect version

CMD ["bash"]
