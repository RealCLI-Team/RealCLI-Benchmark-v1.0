FROM python:3.10-slim-bookworm

# git is needed both for the repository history the task ships with and for
# the grader to restore tracked sources before grading; the rest are the
# usual build conveniences.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY repository/ /app/
WORKDIR /app

# Install docker-py's runtime dependencies and put the package on the import
# path in development mode, so that `import docker` resolves to the working
# tree under /app -- the tree the agent edits. --no-deps keeps the environment
# hermetic and offline; the grader never talks to a real Docker daemon.
RUN pip install --no-cache-dir "requests>=2.26.0" "urllib3>=1.26.0" \
        "paramiko>=2.4.3" \
    && pip install --no-cache-dir --no-deps -e .

# The grader relies on `git checkout` to restore tracked files, so make sure
# the repository is a usable git checkout inside the image.
RUN git config --global --add safe.directory /app

CMD ["/bin/bash"]
