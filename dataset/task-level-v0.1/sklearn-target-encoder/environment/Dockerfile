FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ patch \
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

COPY repository/ /app/
WORKDIR /app

# Build the C extensions in parallel first, then install editable so that
# /app/sklearn is importable in place.
RUN python setup.py build_ext --inplace -j 4 \
    && pip install --no-cache-dir -e . --no-build-isolation

CMD ["/bin/bash"]
