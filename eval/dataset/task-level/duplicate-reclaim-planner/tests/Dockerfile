# Verifier image. Mirrors environment/Dockerfile so the separate verifier
# container has the same runtime as the agent image. At verify time /app and
# /workspace are re-materialized from the agent artifacts; this directory is
# also baked into /tests and re-uploaded before verification.
FROM python:3.10-slim-bookworm

# git is needed both for the repository history the task ships with and for the
# grader to restore tracked sources before grading; gcc/build tools are here so
# that the working tree is a fully usable checkout, but the reclaim planner and
# its grader are pure Python and never touch dupeGuru's optional C extensions.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*

# Source repository: https://github.com/arsenetar/dupeguru.git @ 16aa6c21ffc2c33d44ff4a47bfa1a623c16ed626
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git clone -q --no-checkout https://github.com/arsenetar/dupeguru.git /app \
    && git -C /app checkout -q 16aa6c21ffc2c33d44ff4a47bfa1a623c16ed626
WORKDIR /app

# dupeGuru's core depends only on a handful of pure-Python libraries at runtime.
# We install exactly the ones the reclaim planner exercises -- xxhash for the
# fast content digest that core.fs prefers, Send2Trash for the optional "trash"
# reclaim action -- plus the rest of the declared core deps so `import core.*`
# works everywhere. --no-cache-dir keeps the image lean; the grader runs fully
# offline and never talks to a real Docker daemon or the network.
RUN pip install --no-cache-dir \
        "xxhash>=3.0.0,<4.0.0" \
        "Send2Trash>=1.8.2,<2.0.0" \
        "mutagen>=1.46.0,<2.0.0" \
        "semantic-version>=2.0.0,<3.0.0" \
        "distro>=1.8.0,<2.0.0" \
        "polib>=1.1.0,<2.0.0"

# The grader relies on `git checkout` to restore tracked files, so make sure the
# repository is a usable git checkout inside the image.
RUN git config --global --add safe.directory /app

CMD ["/bin/bash"]

# Bake the verifier scripts into the image (harbor re-uploads them at verify time).
COPY . /tests
