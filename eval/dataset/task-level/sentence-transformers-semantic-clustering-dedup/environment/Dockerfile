FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

RUN apt-get update \
    && apt-get install -y --no-install-recommends patch git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install "pip==24.2" "setuptools==78.1.0" "wheel==0.45.1"

# CPU-only torch, pinned (installed from the dedicated PyTorch CPU index)
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch==2.9.1"

RUN pip install \
    "transformers==5.15.1" \
    "tokenizers==0.22.2" \
    "huggingface-hub==1.28.0" \
    "numpy==1.26.4" \
    "scikit-learn==1.9.0" \
    "scipy==1.17.1" \
    "tqdm==4.70.0" \
    "typing_extensions==4.16.0" \
    "safetensors==0.8.0" \
    "regex==2026.7.19" \
    "pyyaml==6.0.3" \
    "filelock==3.32.3" \
    "joblib==1.5.3" \
    "threadpoolctl==3.6.0" \
    "pytest==8.1.1"

# Source repository: https://github.com/UKPLab/sentence-transformers.git @ 56c9548fd8dbef8ba492f57ac04366900e36f5d4
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git init -q /app \
    && git -C /app remote add origin https://github.com/UKPLab/sentence-transformers.git \
    && git -C /app fetch -q --depth 1 origin 56c9548fd8dbef8ba492f57ac04366900e36f5d4 \
    && git -C /app checkout -q FETCH_HEAD \
    && rm -rf /app/.git
WORKDIR /app

# Install sentence-transformers in editable mode so that /app is importable in
# place; all runtime dependencies are already pinned above.
RUN pip install -e . --no-deps --no-build-isolation

CMD ["/bin/bash"]
