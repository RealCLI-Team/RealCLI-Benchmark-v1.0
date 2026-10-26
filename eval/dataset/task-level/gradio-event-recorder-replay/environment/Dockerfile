FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

RUN apt-get update \
    && apt-get install -y --no-install-recommends patch git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    GRADIO_ANALYTICS_ENABLED=False \
    PYTHONPATH=/app:/app/client/python

RUN pip install "pip==24.2"

RUN pip install \
    "aiofiles==24.1.0" \
    "anyio==4.14.2" \
    "brotli==1.2.0" \
    "fastapi==0.141.1" \
    "ffmpy==1.0.0" \
    "fsspec==2026.7.0" \
    "groovy==0.1.2" \
    "httpx==0.28.1" \
    "huggingface_hub==1.28.0" \
    "Jinja2==3.1.6" \
    "MarkupSafe==3.0.3" \
    "numpy==1.26.4" \
    "orjson==3.12.0" \
    "packaging==26.3" \
    "pandas==2.3.3" \
    "pillow==11.3.0" \
    "pydantic==2.11.10" \
    "pydub==0.25.1" \
    "python-multipart==0.0.32" \
    "PyYAML==6.0.3" \
    "ruff==0.16.4" \
    "safehttpx==0.1.7" \
    "semantic-version==2.10.0" \
    "starlette==0.52.1" \
    "tomlkit==0.13.3" \
    "typer==0.27.1" \
    "typing_extensions==4.16.0" \
    "uvicorn==0.52.4" \
    "websockets==15.0.1" \
    "pytest==8.1.1"

# Source repository: https://github.com/gradio-app/gradio.git @ 45c7d6cc69003d7edf4b610f43efa3f37381d51c
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git init -q /app \
    && git -C /app remote add origin https://github.com/gradio-app/gradio.git \
    && git -C /app fetch -q --depth 1 origin 45c7d6cc69003d7edf4b610f43efa3f37381d51c \
    && git -C /app checkout -q FETCH_HEAD \
    && rm -rf /app/.git
WORKDIR /app
