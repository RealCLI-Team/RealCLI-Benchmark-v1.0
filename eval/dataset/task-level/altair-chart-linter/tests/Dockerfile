# Verifier image. Mirrors environment/Dockerfile so the separate verifier
# container has the same runtime as the agent image. At verify time /app and
# /workspace are re-materialized from the agent artifacts; this directory is
# also baked into /tests and re-uploaded before verification.
# Altair chart-linter task environment.
# Base snapshot: vega/altair @ fde4b47a (v6 development tree, pure Python).
FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

RUN apt-get update \
    && apt-get install -y --no-install-recommends patch git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Runtime dependencies of the snapshot (see pyproject.toml [project]
# dependencies), pinned, plus pytest for the test suite. Everything is
# installed at image build time; nothing is fetched at runtime.
RUN pip install --no-cache-dir \
        "jsonschema==4.25.1" \
        "jinja2==3.1.6" \
        "packaging==25.0" \
        "narwhals==2.5.0" \
        "typing_extensions==4.15.0" \
        "pytest==8.1.1"

# Source repository: https://github.com/vega/altair.git @ fde4b47a4eb219dd51aeff934e5289faab587a19
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git init -q /app \
    && git -C /app remote add origin https://github.com/vega/altair.git \
    && git -C /app fetch -q --depth 1 origin fde4b47a4eb219dd51aeff934e5289faab587a19 \
    && git -C /app checkout -q FETCH_HEAD \
    && rm -rf /app/.git
WORKDIR /app

# The upstream build backend (hatchling + versioningit) derives the package
# version from git metadata, which is not available in this snapshot. The
# package itself is pure Python, so make it importable the way an editable
# install would: a .pth entry pointing at /app plus a minimal dist-info so
# that importlib.metadata.version("altair") works.
RUN SP="$(python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')" \
    && echo "/app" > "$SP/altair-dev.pth" \
    && mkdir -p "$SP/altair-6.0.0.dev0.dist-info" \
    && printf 'Metadata-Version: 2.1\nName: altair\nVersion: 6.0.0.dev0\n' \
        > "$SP/altair-6.0.0.dev0.dist-info/METADATA" \
    && cd /tmp && python -c "import altair; print(altair.__version__)"

CMD ["bash"]

# Bake the verifier scripts into the image (harbor re-uploads them at verify time).
COPY . /tests
