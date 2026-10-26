FROM rocker/r-ver:4.3.1

# python3 is needed by the evidence collector (run_hard_checks.py).
RUN apt-get update && apt-get install -y python3 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Offline: use only base R + stats. No network package installs.
COPY inputs/data/ /app/inputs/data/

CMD ["bash"]
