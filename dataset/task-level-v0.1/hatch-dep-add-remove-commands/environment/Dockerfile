FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SETUPTOOLS_SCM_PRETEND_VERSION=1.18.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends patch=2.7.6-7 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-pinned.txt /tmp/requirements-pinned.txt
RUN pip install --no-cache-dir -r /tmp/requirements-pinned.txt

COPY repository/ /app/
RUN pip install --no-cache-dir --no-build-isolation --no-deps -e /app/backend -e /app

WORKDIR /app
CMD ["/bin/bash"]
