# Verifier image. Mirrors environment/Dockerfile so the separate verifier
# container has the same runtime as the agent image. At verify time /app and
# /workspace are re-materialized from the agent artifacts; this directory is
# also baked into /tests and re-uploaded before verification.
FROM python:3.10-slim-bookworm

RUN apt-get update && apt-get install -y \
    tmux asciinema gcc g++ git wget curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Preinstall the verifier toolchain so tests run fully offline
# (test.sh would otherwise pip/uv-install these at run time).
RUN pip install pyyaml==6.0.2 pytest==8.4.1 pytest-json-ctrf==0.3.5

# Copy pipeline fixtures into the image
COPY pipelines/ /app/pipelines/


# Bake the verifier scripts into the image (harbor re-uploads them at verify time).
COPY . /tests
