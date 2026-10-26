# syntax=docker/dockerfile:1
# Publicly buildable agent base image.
# Every base image comes from Docker Hub; Python and npm dependencies are pinned.

ARG NODE_IMAGE=node:22-bookworm-slim
ARG PYTHON_IMAGE=python:3.13-slim-bookworm

FROM ${NODE_IMAGE} AS tools
ARG CLAUDE_VERSION=2.1.150
ARG OPENCODE_VERSION=1.14.18
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_VERSION} opencode-ai@${OPENCODE_VERSION} \
    && claude --version \
    && opencode --version

FROM ${PYTHON_IMAGE}
ARG OPENCODE_VERSION=1.14.18
USER root

COPY --from=tools /usr/local/ /usr/local/

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        bash ca-certificates curl git jq file procps ripgrep sqlite3 tini unzip zip \
        libreoffice-writer libreoffice-calc libreoffice-impress poppler-utils \
        tesseract-ocr tesseract-ocr-eng fontconfig fonts-dejavu-core fonts-liberation \
        fonts-noto-core fonts-noto-cjk fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /opt/runtime/requirements.txt
RUN pip install --no-cache-dir -r /opt/runtime/requirements.txt \
    && pip check

# The verifier renders HTML deliverables with headless Chromium.
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        chromium \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 10001 clibench \
    && useradd -m -u 10001 -g 10001 -s /bin/bash clibench \
    && mkdir -p /workspace/task_folder /workspace/materials /workspace/output /workspace/model_output \
        /logs/agent /logs/verifier /logs/artifacts \
        /home/clibench/.config/opencode /home/clibench/.claude /home/clibench/.cache/matplotlib \
    && chown -R clibench:clibench /workspace /logs /home/clibench

RUN npm install --prefix /home/clibench/.config/opencode --no-audit --no-fund \
        @opencode-ai/plugin@${OPENCODE_VERSION} \
    && chown -R clibench:clibench /home/clibench/.config/opencode

ENV HOME=/home/clibench \
    XDG_CONFIG_HOME=/home/clibench/.config \
    XDG_CACHE_HOME=/home/clibench/.cache \
    MPLCONFIGDIR=/home/clibench/.cache/matplotlib \
    PYTHONUNBUFFERED=1 \
    PYTHONNOUSERSITE=1 \
    NODE_PATH=/usr/local/lib/node_modules

COPY materials/ /workspace/materials/
RUN chmod -R a+rX /workspace/materials

WORKDIR /workspace
USER clibench
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sleep", "infinity"]
