# Verifier image. Mirrors environment/Dockerfile so the separate verifier
# container has the same runtime as the agent image. At verify time /app and
# /workspace are re-materialized from the agent artifacts; this directory is
# also baked into /tests and re-uploaded before verification.
# grpc-go outlier-detection task environment.
# Base snapshot: grpc/grpc-go @ 182e9df1 (go 1.17 per repository/go.mod;
# toolchain era of the upstream change is go 1.19).
FROM golang:1.19-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        git \
        patch \
        procps \
        python3 \
        python3-pytest \
    && rm -rf /var/lib/apt/lists/*

# Source repository: https://github.com/grpc/grpc-go.git @ 182e9df160798063192858af72aec7148ccd5077
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git init -q /app \
    && git -C /app remote add origin https://github.com/grpc/grpc-go.git \
    && git -C /app fetch -q --depth 1 origin 182e9df160798063192858af72aec7148ccd5077 \
    && git -C /app checkout -q FETCH_HEAD \
    && rm -rf /app/.git
WORKDIR /app

# The snapshot ships no .git directory; keep Go from attempting VCS stamping.
ENV GOFLAGS=-buildvcs=false

# Warm the Go module and build caches so that, after the solution patch is
# applied, incremental rebuilds and test compilation stay fast.
RUN go mod download \
    && go build ./...

CMD ["bash"]

# Sandbox exec drops image ENV PATH; re-expose go via /usr/local/bin (always on PATH).
RUN src=$(ls /usr/local/go/bin/go 2>/dev/null || find / -maxdepth 4 -name go -type f -path '*bin*' 2>/dev/null | head -1); \
    ln -sf "$src" /usr/local/bin/go; ln -sf "$(dirname "$src")/gofmt" /usr/local/bin/gofmt; \
    command -v go


# Bake the verifier scripts into the image (harbor re-uploads them at verify time).
COPY . /tests
