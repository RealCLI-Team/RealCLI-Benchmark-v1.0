FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

RUN apt-get update \
    && apt-get install -y --no-install-recommends patch git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install "pip==24.2" "setuptools==69.0.3" "wheel==0.42.0"

RUN pip install \
    "numpy==2.2.6" \
    "pyarrow==21.0.0" \
    "dill==0.4.0" \
    "multiprocess==0.70.18" \
    "pandas==2.3.1" \
    "requests==2.32.4" \
    "httpx==0.28.1" \
    "tqdm==4.67.1" \
    "xxhash==3.5.0" \
    "fsspec[http]==2025.7.0" \
    "aiohttp==3.12.15" \
    "huggingface-hub==0.34.4" \
    "filelock==3.18.0" \
    "packaging==25.0" \
    "pyyaml==6.0.2" \
    "openpyxl==3.1.5" \
    "xlrd==2.0.1" \
    "xlwt==1.3.0" \
    "pytest==8.3.5"

# Source repository: https://github.com/huggingface/datasets.git @ 836b82e0544cabf6474b25ade131b4d21e570373
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git init -q /app \
    && git -C /app remote add origin https://github.com/huggingface/datasets.git \
    && git -C /app fetch -q --depth 1 origin 836b82e0544cabf6474b25ade131b4d21e570373 \
    && git -C /app checkout -q FETCH_HEAD \
    && rm -rf /app/.git
WORKDIR /app

# Install datasets in editable mode so that /app/src is importable in place;
# all runtime dependencies are already pinned above.
RUN pip install -e . --no-deps --no-build-isolation

CMD ["/bin/bash"]
