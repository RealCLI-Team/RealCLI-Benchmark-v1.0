FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

RUN apt-get update \
    && apt-get install -y --no-install-recommends patch git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Source repository: https://github.com/scrapy/scrapy.git @ 2216063c063027d541eafbf5e098557ce732528f
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git init -q /app \
    && git -C /app remote add origin https://github.com/scrapy/scrapy.git \
    && git -C /app fetch -q --depth 1 origin 2216063c063027d541eafbf5e098557ce732528f \
    && git -C /app checkout -q FETCH_HEAD \
    && rm -rf /app/.git
COPY requirements.txt /tmp/requirements.txt
WORKDIR /app

RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && pip install --no-cache-dir hatchling==1.27.0 editables==0.5 \
    && pip install --no-cache-dir --no-deps --no-build-isolation -e .

CMD ["/bin/bash"]
