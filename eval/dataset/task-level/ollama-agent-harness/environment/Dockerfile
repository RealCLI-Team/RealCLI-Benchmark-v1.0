# ollama agent-harness task environment.
# Base snapshot: ollama/ollama @ 624cada9 (go 1.26 per repository/go.mod).
FROM golang:1.26-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        patch \
        procps \
        python3 \
        python3-pytest \
    && rm -rf /var/lib/apt/lists/*

# Source repository: https://github.com/ollama/ollama.git @ 624cada952c87bcf1f0e7d00b669f0cc483103eb
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git init -q /app \
    && git -C /app remote add origin https://github.com/ollama/ollama.git \
    && git -C /app fetch -q --depth 1 origin 624cada952c87bcf1f0e7d00b669f0cc483103eb \
    && git -C /app checkout -q FETCH_HEAD \
    && rm -rf /app/.git
WORKDIR /app

# The snapshot ships no .git directory; keep Go from attempting VCS stamping.
ENV GOFLAGS=-buildvcs=false

# Warm the Go module and build caches so that, after the solution is applied,
# an incremental rebuild and the contract tests stay fast.
RUN go mod download \
    && go build ./...

CMD ["bash"]

# Sandbox exec drops image ENV PATH; re-expose go via /usr/local/bin (always on PATH).
RUN src=$(ls /usr/local/go/bin/go 2>/dev/null || find / -maxdepth 4 -name go -type f -path '*bin*' 2>/dev/null | head -1); \
    ln -sf "$src" /usr/local/bin/go; ln -sf "$(dirname "$src")/gofmt" /usr/local/bin/gofmt; \
    command -v go

