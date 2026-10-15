FROM mcr.microsoft.com/playwright:v1.61.0-noble
WORKDIR /3D-Global-Flight-Tracker
# Browsers are baked into the base image at PLAYWRIGHT_BROWSERS_PATH; make it
# explicit so the render gate finds Chromium even if the base drops the env.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN apt-get update && apt-get install -y python3 python3-pip && rm -rf /var/lib/apt/lists/*
# Pin the pip package to the SAME version as the base image tag (1.61.0),
# so the baked Chromium build matches and launches with no re-download.
RUN pip3 install --no-cache-dir --break-system-packages playwright==1.61.0
# Node.js + Chromium/Firefox/WebKit (and system deps) are already in the base image.
# Serves & renders HTML / React / Vue / Astro / three.js artifacts.
# The workspace starts empty: the agent receives the task inline (claude -p ...)
# and writes its artifact here, so a non-empty workspace means real agent output.
