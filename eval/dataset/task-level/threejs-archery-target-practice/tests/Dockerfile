# Verifier image. Mirrors environment/Dockerfile so the separate verifier
# container has the same runtime as the agent image. At verify time /app and
# /workspace are re-materialized from the agent artifacts; this directory is
# also baked into /tests and re-uploaded before verification.
FROM mcr.microsoft.com/playwright:v1.61.0-noble
WORKDIR /task_0000
# Chromium is baked at PLAYWRIGHT_BROWSERS_PATH; the baked scorer's node playwright
# (tests/scorer/node_modules, pinned to 1.61.0) launches it with no download.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
# python3 only (for tests/scorer/aggregate.py reward shaping). No pip playwright:
# the OFFICIAL scorer is Node and ships its own playwright in tests/scorer/.
RUN apt-get update && apt-get install -y python3 && rm -rf /var/lib/apt/lists/*
# Node.js + Chromium (and system deps) are already in the base image. The
# workspace starts empty: the agent writes its artifact here; tests/test.sh then
# runs the official Node scorer against it.

# Bake the verifier scripts into the image (harbor re-uploads them at verify time).
COPY . /tests
