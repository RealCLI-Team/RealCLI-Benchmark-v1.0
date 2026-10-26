# Verifier image. Mirrors environment/Dockerfile so the separate verifier
# container has the same runtime as the agent image. At verify time /app and
# /workspace are re-materialized from the agent artifacts; this directory is
# also baked into /tests and re-uploaded before verification.
FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

RUN apt-get update \
    && apt-get install -y --no-install-recommends patch git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    "numpy==1.26.4" \
    "pandas==2.2.2" \
    "scipy==1.17.1" \
    "woodwork==0.28.0" \
    "cloudpickle==3.1.2" \
    "holidays==0.103" \
    "packaging==26.3" \
    "psutil==7.2.2" \
    "tqdm==4.70.0" \
    "click==8.4.2" \
    "python-dateutil==2.9.0.post0" \
    "setuptools==75.8.0" \
    "pytest==8.1.1"

# Source repository: https://github.com/alteryx/featuretools.git @ ba4bdab6ef4b57b486af83029e81b13a6349a00e
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git init -q /app \
    && git -C /app remote add origin https://github.com/alteryx/featuretools.git \
    && git -C /app fetch -q --depth 1 origin ba4bdab6ef4b57b486af83029e81b13a6349a00e \
    && git -C /app checkout -q FETCH_HEAD \
    && rm -rf /app/.git
WORKDIR /app

# Install featuretools in editable mode so that /app/featuretools is the
# importable package and in-place code changes take effect immediately.
RUN pip install --no-cache-dir -e . --no-deps

CMD ["/bin/bash"]

# Bake the verifier scripts into the image (harbor re-uploads them at verify time).
COPY . /tests
