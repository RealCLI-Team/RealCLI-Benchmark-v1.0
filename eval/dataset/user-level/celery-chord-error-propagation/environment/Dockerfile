FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

RUN apt-get update \
    && apt-get install -y --no-install-recommends git patch \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    setuptools==75.8.0 \
    wheel==0.45.1 \
    billiard==4.2.4 \
    kombu==5.5.4 \
    vine==5.1.0 \
    amqp==5.3.1 \
    click==8.4.2 \
    click-didyoumean==0.3.1 \
    click-plugins==1.1.1.2 \
    click-repl==0.3.0 \
    prompt_toolkit==3.0.53 \
    wcwidth==0.8.2 \
    python-dateutil==2.9.0.post0 \
    six==1.16.0 \
    tzdata==2026.3 \
    redis==8.1.0 \
    PyYAML==6.0.2 \
    msgpack==1.1.0 \
    pytest==8.1.1 \
    pytest-timeout==2.3.1 \
    pluggy==1.6.0 \
    iniconfig==2.3.0 \
    packaging==26.3

# Source repository: https://github.com/celery/celery.git @ 088c39c0f78b23a9cdf8d1c9e265ea64d02cfd86
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git init -q /app \
    && cd /app \
    && git remote add origin https://github.com/celery/celery.git \
    && git fetch -q --depth 1 origin 088c39c0f78b23a9cdf8d1c9e265ea64d02cfd86 \
    && git checkout -q FETCH_HEAD
WORKDIR /app

RUN pip install --no-cache-dir --no-build-isolation --no-deps -e .

CMD ["/bin/bash"]
