FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

RUN apt-get update \
    && apt-get install -y --no-install-recommends patch git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    pytest==8.1.1

# Source repository: https://github.com/keon/algorithms.git @ e46c1a5f543c1d71e687598a277df2e3d075583a
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git init -q /app \
    && git -C /app remote add origin https://github.com/keon/algorithms.git \
    && git -C /app fetch -q --depth 1 origin e46c1a5f543c1d71e687598a277df2e3d075583a \
    && git -C /app checkout -q FETCH_HEAD \
    && rm -rf /app/.git
WORKDIR /app
