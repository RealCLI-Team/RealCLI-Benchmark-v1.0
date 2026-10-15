FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1

# patch is required by the oracle apply step.
RUN apt-get update \
    && apt-get install -y --no-install-recommends patch \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY repository/ /app/

# Runtime + test dependencies, then an editable install of the repo itself so
# that agent edits under /app take effect immediately. compileall bakes the
# bytecode cache into the image to warm up later runs.
RUN pip install --no-cache-dir "mpmath>=0.19" pytest \
    && pip install --no-cache-dir -e . \
    && python -m compileall -q sympy \
    && python -c "import sympy; from sympy import QQ, sqrt; K = QQ.algebraic_field(sqrt(2)); print(sympy.__version__, K)"
