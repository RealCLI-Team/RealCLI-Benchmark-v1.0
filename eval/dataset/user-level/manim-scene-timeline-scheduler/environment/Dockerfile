FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Build/runtime system dependencies:
# - build-essential + pkg-config: compile pycairo and ManimPango from source
# - libcairo2-dev / libpango1.0-dev: native libraries manim renders with
# - patch / git: apply changes and inspect the repository inside the container
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        patch=2.7.6-7 \
        git=1:2.39.5-0+deb12u3 \
        build-essential=12.9 \
        pkg-config=1.8.1-1 \
        libcairo2-dev=1.16.0-7 \
        libpango1.0-dev=1.50.12+ds-1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Source repository: https://github.com/ManimCommunity/manim.git @ 1dc796e9652273950d9863b35746c7329888e384
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git init -q /app \
    && cd /app \
    && git remote add origin https://github.com/ManimCommunity/manim.git \
    && git fetch -q --depth 1 origin 1dc796e9652273950d9863b35746c7329888e384 \
    && git checkout -q FETCH_HEAD
WORKDIR /app
RUN pip install --no-cache-dir --no-deps -e .

CMD ["/bin/bash"]
