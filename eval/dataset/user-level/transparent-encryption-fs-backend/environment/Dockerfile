FROM python:3.10-slim-bookworm

# git is needed both for the repository history the task ships with and for
# the grader to restore tracked sources; patch is a convenience for solve.sh.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git patch ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Source repository: https://github.com/PyFilesystem/pyfilesystem2.git @ 50b1c9956ee0c674c93ffbf675a7b0f58db2564d
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git clone -q --no-checkout https://github.com/PyFilesystem/pyfilesystem2.git /app \
    && git -C /app checkout -q 50b1c9956ee0c674c93ffbf675a7b0f58db2564d
WORKDIR /app

# Install the library's runtime dependencies and put the package on the
# import path in development mode, so that `import fs` resolves to the
# working tree under /app (the tree the agent edits).
RUN pip install --no-cache-dir "six~=1.16" "appdirs~=1.4" \
    && pip install --no-cache-dir --no-deps -e .

# Make sure the repository is a usable git checkout inside the image
# (the grader relies on `git checkout` to restore tracked files).
RUN git config --global --add safe.directory /app

CMD ["/bin/bash"]
