FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

RUN apt-get update \
    && apt-get install -y --no-install-recommends patch=2.7.6-7 locales \
    && rm -rf /var/lib/apt/lists/*

RUN locale-gen en_US.UTF-8

RUN pip install --no-cache-dir \
    altair==5.4.1 \
    cryptography==43.0.1 \
    jinja2==3.1.4 \
    jsonschema==4.23.0 \
    marshmallow==3.20.1 \
    mistune==3.0.2 \
    numpy==2.0.2 \
    packaging==24.1 \
    pandas==2.2.3 \
    pydantic==1.10.21 \
    pyparsing==3.1.4 \
    python-dateutil==2.9.0.post0 \
    requests==2.32.3 \
    ruamel.yaml==0.18.6 \
    scipy==1.14.1 \
    tqdm==4.66.5 \
    typing-extensions==4.12.2 \
    tzlocal==5.2 \
    pytest==8.3.3 \
    pytest-mock==3.14.0 \
    setuptools==75.1.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Source repository: https://github.com/great-expectations/great_expectations.git @ b94159e0d5899192dca365e47289671ab94ecafc
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git init -q /app \
    && cd /app \
    && git remote add origin https://github.com/great-expectations/great_expectations.git \
    && git fetch -q --depth 1 origin b94159e0d5899192dca365e47289671ab94ecafc \
    && git checkout -q FETCH_HEAD
WORKDIR /app
ENV PYTHONPATH=/app

RUN python -c "import great_expectations; import great_expectations.expectations as gxe; assert hasattr(gxe, 'ExpectColumnValuesToMatchRegex')"
