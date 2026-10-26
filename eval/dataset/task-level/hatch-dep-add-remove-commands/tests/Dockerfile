# Verifier image. Mirrors environment/Dockerfile so the separate verifier
# container has the same runtime as the agent image. At verify time /app and
# /workspace are re-materialized from the agent artifacts; this directory is
# also baked into /tests and re-uploaded before verification.
FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SETUPTOOLS_SCM_PRETEND_VERSION=1.18.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends patch=2.7.6-7 git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-pinned.txt /tmp/requirements-pinned.txt
RUN pip install --no-cache-dir -r /tmp/requirements-pinned.txt

# Source repository: https://github.com/pypa/hatch.git @ 558061c5ecbe7e9b10d81965d4cdc8df05832228
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git init -q /app \
    && git -C /app remote add origin https://github.com/pypa/hatch.git \
    && git -C /app fetch -q --depth 1 origin 558061c5ecbe7e9b10d81965d4cdc8df05832228 \
    && git -C /app checkout -q FETCH_HEAD \
    && rm -rf /app/.git
RUN pip install --no-cache-dir --no-build-isolation --no-deps -e /app/backend -e /app

WORKDIR /app
CMD ["/bin/bash"]

# Bake the verifier scripts into the image (harbor re-uploads them at verify time).
COPY . /tests
