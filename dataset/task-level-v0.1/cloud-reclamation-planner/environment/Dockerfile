FROM python:3.10-slim-bookworm

# git is needed both for the repository history the task ships with and for the
# grader to restore tracked sources before grading. build-essential is here only
# so the working tree is a fully usable checkout; the reclamation planner and its
# grader are pure Python and touch no native extensions and no network.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY repository/ /app/
WORKDIR /app

# Apache Libcloud is installed from the shipped checkout in editable mode so that
# `import libcloud` and its pure-Python resource model resolve exactly as they do
# in the pristine snapshot. The reclamation planner builds only on Libcloud's own
# model classes (Node / StorageVolume / VolumeSnapshot / Container) and never
# calls a cloud API; the grader runs fully offline. --no-cache-dir keeps the
# image lean.
RUN pip install --no-cache-dir -e .

# The grader relies on `git checkout` to restore tracked files, so make sure the
# repository is a usable git checkout inside the image.
RUN git config --global --add safe.directory /app

CMD ["/bin/bash"]
