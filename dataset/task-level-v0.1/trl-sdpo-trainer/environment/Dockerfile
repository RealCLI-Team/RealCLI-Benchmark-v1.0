# TRL SDPO trainer task environment.
# Base snapshot: huggingface/trl @ d8a2dd56 (transformers 4.56 era, torch 2.8).
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        patch \
        procps \
    && rm -rf /var/lib/apt/lists/*

# PyTorch (CPU-only build) in its own layer: it is by far the largest
# download, so keep it cached independently of the repository contents.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
        torch==2.8.0

COPY repository/ /app/
WORKDIR /app

# Editable install of TRL so that later source edits under /app take effect
# without a reinstall. Dependency versions are pinned to the repository era
# (mirroring the project's minimum-versions CI job). Pytest and pytest-timeout
# are the test-runner requirements. Everything is installed up front so the
# agent starts from a warm dependency cache.
RUN pip install --no-cache-dir -e . \
        "transformers==4.56.2" \
        "accelerate==1.4.0" \
        "datasets==3.0.0" \
        "pytest==8.3.5" \
        "pytest-timeout==2.3.1" \
    && python -c "import trl; print('trl', trl.__version__)"

CMD ["bash"]
