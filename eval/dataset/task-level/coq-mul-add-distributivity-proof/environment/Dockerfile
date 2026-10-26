FROM python:3.11-slim

WORKDIR /app

# Install Coq (provides coqc, coqchk) for offline proof checking.
RUN apt-get update \
    && apt-get install -y --no-install-recommends coq \
    && rm -rf /var/lib/apt/lists/*

# Fixture only; do not copy solution/tests/instruction/task files.
COPY inputs/data/ /app/inputs/

CMD ["/bin/bash"]
