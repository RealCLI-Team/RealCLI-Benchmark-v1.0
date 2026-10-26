FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

ENV PDM_CHECK_UPDATE=0 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PDM_BUILD_SCM_VERSION=2.28.2

RUN apt-get update \
    && apt-get install -y --no-install-recommends patch=2.7.6-7 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-pinned.txt /tmp/requirements-pinned.txt
RUN pip install --no-cache-dir -r /tmp/requirements-pinned.txt

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Source repository: https://github.com/pdm-project/pdm.git @ a7e9d0015b4d5ca5d1b0c558582f9bf41b2fdf54
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git init -q /app \
    && cd /app \
    && git remote add origin https://github.com/pdm-project/pdm.git \
    && git fetch -q --depth 1 origin a7e9d0015b4d5ca5d1b0c558582f9bf41b2fdf54 \
    && git checkout -q FETCH_HEAD
RUN pip install --no-cache-dir --no-build-isolation --no-deps -e /app

WORKDIR /app
CMD ["/bin/bash"]
