FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

RUN apt-get update \
    && apt-get install -y --no-install-recommends patch git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Source repository: https://github.com/tobymao/sqlglot.git @ 9a8129b6f2667673f24713f4b49162ebae1f699d
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git init -q /app \
    && git -C /app remote add origin https://github.com/tobymao/sqlglot.git \
    && git -C /app fetch -q --depth 1 origin 9a8129b6f2667673f24713f4b49162ebae1f699d \
    && git -C /app checkout -q FETCH_HEAD \
    && rm -rf /app/sqlglot-integration-tests /app/.git
WORKDIR /app

RUN pip install --no-cache-dir setuptools==80.9.0 setuptools-scm==8.3.1 wheel==0.45.1 \
    && pip install --no-cache-dir pytest==8.1.1 \
    && SETUPTOOLS_SCM_PRETEND_VERSION=30.17.0 pip install --no-cache-dir --no-deps --no-build-isolation -e .

CMD ["/bin/bash"]
