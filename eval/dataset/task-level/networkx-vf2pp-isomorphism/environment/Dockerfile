FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

RUN apt-get update \
    && apt-get install -y --no-install-recommends patch git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    numpy==1.26.4 \
    scipy==1.13.1 \
    pandas==2.2.2 \
    matplotlib==3.9.2 \
    pytest==8.1.1

# Source repository: https://github.com/networkx/networkx.git @ 6e8c3448a1c0d2433b7191f53f287b14b6708a79
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git init -q /app \
    && git -C /app remote add origin https://github.com/networkx/networkx.git \
    && git -C /app fetch -q --depth 1 origin 6e8c3448a1c0d2433b7191f53f287b14b6708a79 \
    && git -C /app checkout -q FETCH_HEAD \
    && rm -rf /app/.git
WORKDIR /app
