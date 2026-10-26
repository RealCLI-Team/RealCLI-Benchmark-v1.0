# Verifier image. Mirrors environment/Dockerfile so the separate verifier
# container has the same runtime as the agent image. At verify time /app and
# /workspace are re-materialized from the agent artifacts; this directory is
# also baked into /tests and re-uploaded before verification.
FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

RUN apt-get update \
    && apt-get install -y --no-install-recommends patch git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install "pip==24.2" "setuptools==69.0.3" "wheel==0.42.0"

RUN pip install \
    "altair==5.1.2" \
    "blinker==1.7.0" \
    "cachetools==5.3.2" \
    "click==8.1.7" \
    "importlib-metadata==6.8.0" \
    "numpy==1.26.2" \
    "packaging==23.2" \
    "pandas==2.1.3" \
    "pillow==10.1.0" \
    "protobuf==4.25.1" \
    "pyarrow==14.0.1" \
    "python-dateutil==2.8.2" \
    "requests==2.31.0" \
    "rich==13.7.0" \
    "tenacity==8.2.3" \
    "toml==0.10.2" \
    "typing-extensions==4.8.0" \
    "tzlocal==5.2" \
    "validators==0.22.0" \
    "watchdog==3.0.0" \
    "gitpython==3.1.40" \
    "pydeck==0.8.1b0" \
    "tornado==6.4" \
    "grpcio-tools==1.59.3" \
    "pytest==7.4.3" \
    "parameterized==0.9.0" \
    "hypothesis==6.92.2" \
    "testfixtures==7.2.2"

# Source repository: https://github.com/streamlit/streamlit.git @ 49770b7484d72e95dd5bc8b8f803b0984ba2bf62
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git init -q /app \
    && git -C /app remote add origin https://github.com/streamlit/streamlit.git \
    && git -C /app fetch -q --depth 1 origin 49770b7484d72e95dd5bc8b8f803b0984ba2bf62 \
    && git -C /app checkout -q FETCH_HEAD \
    && rm -rf /app/.git
WORKDIR /app

# Generate the Python protobuf bindings checked out from source (they are
# build artifacts, not committed), then install streamlit in editable mode.
RUN python -m grpc_tools.protoc \
        --proto_path=proto \
        --python_out=lib \
        proto/streamlit/proto/*.proto \
 && pip install -e lib --no-deps --no-build-isolation

# Bake the verifier scripts into the image (harbor re-uploads them at verify time).
COPY . /tests
