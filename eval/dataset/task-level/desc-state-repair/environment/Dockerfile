FROM ubuntu:24.04

RUN apt-get update && apt-get install -y \
    python3 \
    python3-pytest \
    coreutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY input /app/input
