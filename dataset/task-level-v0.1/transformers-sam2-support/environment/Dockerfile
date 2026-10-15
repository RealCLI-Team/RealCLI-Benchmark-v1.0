# transformers SAM2 support task environment.
# Base snapshot: huggingface/transformers @ 25ad9c8c (v4.56.0.dev0, python_requires >= 3.9).
FROM python:3.11-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        patch \
    && rm -rf /var/lib/apt/lists/*

COPY repository/ /app/
WORKDIR /app

# CPU-only PyTorch stack (torch + torchvision: the fast image processors rely
# on torchvision ops). Installed from the PyTorch CPU wheel index to keep the
# image small and CUDA-free.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        torch==2.7.1 \
        torchvision==0.22.1 \
        --index-url https://download.pytorch.org/whl/cpu

# Editable install of the snapshot plus the runtime/test dependencies that are
# not part of the core install set. Editable mode keeps /app as the live
# source tree, so agent edits take effect without reinstalling.
RUN pip install --no-cache-dir -e . \
    && pip install --no-cache-dir \
        Pillow \
        pytest

# Warm the Python bytecode caches so importing the library during the task is
# faster; also verifies the base snapshot imports cleanly at build time.
RUN python -c "import transformers; from transformers import SamModel, SamProcessor, AutoConfig, AutoModel; print(transformers.__version__)"

CMD ["bash"]
