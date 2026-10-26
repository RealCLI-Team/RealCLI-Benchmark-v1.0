FROM mcr.microsoft.com/playwright:v1.61.0-noble
WORKDIR /task_0000
# Chromium is baked at PLAYWRIGHT_BROWSERS_PATH; the baked scorer's node playwright
# (tests/scorer/node_modules, pinned to 1.61.0) launches it with no download.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
# python3 only (for tests/scorer/aggregate.py reward shaping). No pip playwright:
# the OFFICIAL scorer is Node and ships its own playwright in tests/scorer/.
RUN apt-get update && apt-get install -y python3 && rm -rf /var/lib/apt/lists/*
# Bake the scorer's Node deps (playwright, pinned 1.61.0) into the image.
# The dataset ships no node_modules and the verify sandbox has no network, so
# without this run_eval.mjs dies with ERR_MODULE_NOT_FOUND and every trial
# scores a fake 0 (2026-09-18 archery/oil-slick 实测全线 NO_ARTIFACT)。
# tests/test.sh symlinks this into /tests/scorer/node_modules at verify time.
RUN mkdir -p /opt/scorer-deps \
    && printf '{"name":"scorer-deps","private":true,"dependencies":{"playwright":"1.61.0"}}' > /opt/scorer-deps/package.json \
    && cd /opt/scorer-deps && npm install --omit=dev --no-audit --no-fund
# Node.js + Chromium (and system deps) are already in the base image. The
# workspace starts empty: the agent writes its artifact here; tests/test.sh then
# runs the official Node scorer against it.
