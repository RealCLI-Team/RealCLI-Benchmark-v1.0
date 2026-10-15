# Prefect cascading-cancellation task environment.
# Base snapshot: PrefectHQ/prefect @ 1c970c3b
# (requires-python >=3.10,<3.15 per repository/pyproject.toml; the snapshot
# ships no .git directory and versioningit falls back to its configured
# default-version, so a git-less editable install works).
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

COPY repository/ /app/
WORKDIR /app

# Install dependencies pinned by the repository's uv.lock (a bare
# `pip install -e .` resolves loose upper bounds to the newest releases,
# which the snapshot's server code is not compatible with), then install
# prefect itself in editable mode. This bakes the full runtime dependency
# set into the image; after the solution patch is applied, only files under
# /app/src change and the editable install picks them up without any
# rebuild.
RUN pip install --no-cache-dir uv \
    && uv export --frozen --no-hashes --no-dev --no-install-project \
        -o /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && pip install --no-cache-dir --no-deps -e . \
    && pip install --no-cache-dir pytest pytest-asyncio \
    && rm -f /tmp/requirements.txt

# Sanity check: the CLI is importable and on PATH.
RUN prefect version

CMD ["bash"]
