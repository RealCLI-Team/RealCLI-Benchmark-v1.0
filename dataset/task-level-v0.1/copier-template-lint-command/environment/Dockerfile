FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# `patch` is needed to apply source patches inside the container and `git` is
# a runtime requirement of copier; neither is part of the slim base image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends patch=2.7.6-7 git=1:2.39.5-0+deb12u3 \
    && rm -rf /var/lib/apt/lists/*

# All runtime, build and verification dependencies, version-pinned. The
# container runs fully offline after the build.
RUN pip install --no-cache-dir \
    colorama==0.4.6 \
    dunamai==1.26.2 \
    funcy==2.0 \
    Jinja2==3.1.6 \
    MarkupSafe==3.0.3 \
    jinja2-ansible-filters==1.3.2 \
    packaging==26.3 \
    pathspec==1.1.1 \
    platformdirs==4.11.4 \
    plumbum==2.0.2 \
    annotated-types==0.8.0 \
    pydantic==2.13.4 \
    pydantic_core==2.46.4 \
    typing-inspection==0.4.4 \
    typing_extensions==4.16.0 \
    Pygments==2.21.0 \
    PyYAML==6.0.3 \
    prompt_toolkit==3.0.53 \
    wcwidth==0.8.2 \
    questionary==2.1.1 \
    rich==15.0.0 \
    markdown-it-py==4.2.0 \
    mdurl==0.1.2 \
    hatchling==1.32.0 \
    hatch-vcs==0.5.0 \
    editables==0.6 \
    setuptools-scm==10.2.1 \
    trove-classifiers==2026.6.1.19 \
    pluggy==1.6.0 \
    iniconfig==2.3.0 \
    pytest==8.1.1

COPY repository/ /app/
WORKDIR /app

# Editable install: the version is pinned because the snapshot has no .git
# directory for hatch-vcs to derive it from.
RUN SETUPTOOLS_SCM_PRETEND_VERSION=9.17.2 pip install --no-deps --no-build-isolation -e .
