FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends patch=2.7.6-7 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        sqlalchemy==2.0.43 \
        mako==1.3.10 \
        typing_extensions==4.16.0 \
        pytest==8.1.1

COPY repository/ /app/
WORKDIR /app

# Sandbox exec drops image ENV; make the alembic CLI + /app imports available
# under the default PATH via an editable install of the repo itself.
RUN pip install --no-cache-dir -e /app

