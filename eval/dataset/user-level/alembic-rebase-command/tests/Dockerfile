# Verifier image. Mirrors environment/Dockerfile so the separate verifier
# container has the same runtime as the agent image. At verify time /app and
# /workspace are re-materialized from the agent artifacts; this directory is
# also baked into /tests and re-uploaded before verification.
FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends patch=2.7.6-7 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        sqlalchemy==2.0.43 \
        mako==1.3.10 \
        typing_extensions==4.16.0 \
        pytest==8.1.1

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Source repository: https://github.com/sqlalchemy/alembic.git @ c026cf70aece7d6cddf929cb37748c6076b1d528
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git init -q /app \
    && cd /app \
    && git remote add origin https://github.com/sqlalchemy/alembic.git \
    && git fetch -q --depth 1 origin c026cf70aece7d6cddf929cb37748c6076b1d528 \
    && git checkout -q FETCH_HEAD
WORKDIR /app

# Sandbox exec drops image ENV; make the alembic CLI + /app imports available
# under the default PATH via an editable install of the repo itself.
RUN pip install --no-cache-dir -e /app


# Bake the verifier scripts into the image (harbor re-uploads them at verify time).
COPY . /tests
