FROM mcr.microsoft.com/playwright:v1.61.0-noble
WORKDIR /archery-target-practice-3d_0000
# Chromium is baked at PLAYWRIGHT_BROWSERS_PATH; the baked scorer's node playwright
# (tests/scorer/node_modules, pinned to 1.61.0) launches it with no download.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
# python3 only (for tests/scorer/aggregate.py reward shaping). No pip playwright:
# the OFFICIAL scorer is Node and ships its own playwright in tests/scorer/.
RUN apt-get update && apt-get install -y python3 && rm -rf /var/lib/apt/lists/*
# Bake per-task assets (GLBs / textures) into the workspace so the agent can
# preview locally with an HTTP server + resolve relative `assets/foo.glb`
# references at authoring time. Source is `environment/assets/` populated at
# export time via hardlinks from `tests/assets/` (same inodes -> zero host disk
# overhead). Empty for tasks without referenced assets. The OFFICIAL scorer
# separately mounts its own `tests/assets/` copy at verify time via --assets-dir.
COPY assets /archery-target-practice-3d_0000/assets
# Node.js + Chromium (and system deps) are already in the base image. Beyond
# assets/, the workspace starts empty: the agent writes its artifact here;
# tests/test.sh then runs the official Node scorer against it.
