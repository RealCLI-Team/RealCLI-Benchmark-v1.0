FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1

# patch is required by the oracle apply step.
RUN apt-get update \
    && apt-get install -y --no-install-recommends patch \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Source repository: https://github.com/sympy/sympy.git @ b2b13055cf15368bafc6bd80c44a865eb86b105b
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git init -q /app \
    && cd /app \
    && git remote add origin https://github.com/sympy/sympy.git \
    && git fetch -q --depth 1 origin b2b13055cf15368bafc6bd80c44a865eb86b105b \
    && git checkout -q FETCH_HEAD

# Runtime + test dependencies, then an editable install of the repo itself so
# that agent edits under /app take effect immediately. compileall bakes the
# bytecode cache into the image to warm up later runs.
RUN pip install --no-cache-dir "mpmath>=0.19" pytest \
    && pip install --no-cache-dir -e . \
    && python -m compileall -q sympy \
    && python -c "import sympy; from sympy import QQ, sqrt; K = QQ.algebraic_field(sqrt(2)); print(sympy.__version__, K)"
