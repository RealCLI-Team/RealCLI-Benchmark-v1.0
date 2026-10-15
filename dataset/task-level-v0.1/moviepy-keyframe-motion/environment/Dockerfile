FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# patch: apply the reference solution; git: inspect the repository
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        patch=2.7.6-7 \
        git=1:2.39.5-0+deb12u3 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY repository/ /app/
WORKDIR /app
RUN pip install --no-cache-dir --no-deps -e .

CMD ["/bin/bash"]
