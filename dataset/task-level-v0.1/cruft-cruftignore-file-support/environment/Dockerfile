FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# `patch` is needed to apply source patches inside the container and `git`
# is a hard runtime dependency of cruft itself (template cloning, diffing
# and patch application). Neither is part of the slim base image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends patch=2.7.6-7 git=1:2.39.5-0+deb12u3 \
    && rm -rf /var/lib/apt/lists/*

# All runtime and verification dependencies, version-pinned. The container
# runs fully offline after the build.
RUN pip install --no-cache-dir \
    setuptools==84.0.0 \
    wheel==0.48.0 \
    annotated-doc==0.0.5 \
    arrow==1.4.0 \
    binaryornot==0.6.0 \
    certifi==2026.7.22 \
    charset-normalizer==3.5.1 \
    click==8.4.2 \
    cookiecutter==2.7.1 \
    gitdb==4.0.12 \
    GitPython==3.1.60 \
    idna==3.19 \
    iniconfig==2.3.0 \
    Jinja2==3.1.6 \
    markdown-it-py==4.2.0 \
    MarkupSafe==3.0.3 \
    mdurl==0.1.2 \
    packaging==26.3 \
    pluggy==1.6.0 \
    Pygments==2.21.0 \
    pytest==8.1.1 \
    python-dateutil==2.9.0.post0 \
    python-slugify==8.0.4 \
    PyYAML==6.0.3 \
    requests==2.34.2 \
    rich==15.0.0 \
    shellingham==1.5.4 \
    six==1.16.0 \
    smmap==5.0.3 \
    text-unidecode==1.3 \
    typer==0.27.1 \
    tzdata==2026.3 \
    urllib3==2.7.0

COPY repository/ /app/
WORKDIR /app

# Editable install: package metadata is available and edits under /app take
# effect immediately. The project uses a static version, so no SCM metadata
# is required.
RUN pip install --no-deps -e .
