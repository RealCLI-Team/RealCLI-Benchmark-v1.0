# Verifier image. Mirrors environment/Dockerfile so the separate verifier
# container has the same runtime as the agent image. At verify time /app and
# /workspace are re-materialized from the agent artifacts; this directory is
# also baked into /tests and re-uploaded before verification.
FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ patch git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    "numpy==1.26.4" \
    "scipy==1.11.4" \
    "Cython==0.29.37" \
    "setuptools==69.0.3" \
    "wheel==0.42.0" \
    "joblib==1.3.2" \
    "threadpoolctl==3.2.0" \
    "pytest==7.4.4" \
    "pandas==2.1.4"

# Source repository: https://github.com/scikit-learn/scikit-learn.git @ d7f62b0d9d97b7f6282e5d3a79f81c325a8d1f64
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git init -q /app \
    && git -C /app remote add origin https://github.com/scikit-learn/scikit-learn.git \
    && git -C /app fetch -q --depth 1 origin d7f62b0d9d97b7f6282e5d3a79f81c325a8d1f64 \
    && git -C /app checkout -q FETCH_HEAD \
    && rm -rf /app/.git
WORKDIR /app

# Build the C extensions in parallel first, then install editable so that
# /app/sklearn is importable in place.
RUN python setup.py build_ext --inplace -j 4 \
    && pip install --no-cache-dir -e . --no-build-isolation

CMD ["/bin/bash"]

# Bake the verifier scripts into the image (harbor re-uploads them at verify time).
COPY . /tests
