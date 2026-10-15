FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

RUN apt-get update \
    && apt-get install -y --no-install-recommends patch \
    && rm -rf /var/lib/apt/lists/*

COPY repository/ /app/
COPY requirements.txt /tmp/requirements.txt
WORKDIR /app

RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && pip install --no-cache-dir hatchling==1.27.0 editables==0.5 \
    && pip install --no-cache-dir --no-deps --no-build-isolation -e .

CMD ["/bin/bash"]
