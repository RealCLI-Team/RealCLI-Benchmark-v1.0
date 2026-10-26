FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash coreutils grep sed gawk \
    && rm -rf /var/lib/apt/lists/*

COPY inputs/ /app/inputs/

CMD ["/bin/bash"]
