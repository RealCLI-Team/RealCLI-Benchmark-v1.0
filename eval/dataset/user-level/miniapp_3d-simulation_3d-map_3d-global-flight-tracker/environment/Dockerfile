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

# Pre-vendor the four UMD libs under the slug folders the script pins
# (./vendor/threejs-vendor, orbitcontrols-vendor, datgui-lite, stats-min).
# The interaction script used to promise "files land on my next pass", but the
# user sim cannot write files mid-session and the verifier's render gate runs
# under net lockdown — so the promised drop is baked in at image build time.
RUN set -e; cd /tmp \
 && npm pack three@0.147.0 dat.gui@0.7.9 stats.js@0.17.0 \
 && mkdir -p /3D-Global-Flight-Tracker/vendor/threejs-vendor \
             /3D-Global-Flight-Tracker/vendor/orbitcontrols-vendor \
             /3D-Global-Flight-Tracker/vendor/datgui-lite \
             /3D-Global-Flight-Tracker/vendor/stats-min \
 && tar -xzf three-0.147.0.tgz \
 && cp package/build/three.min.js /3D-Global-Flight-Tracker/vendor/threejs-vendor/three.min.js \
 && cp package/examples/js/controls/OrbitControls.js /3D-Global-Flight-Tracker/vendor/orbitcontrols-vendor/OrbitControls.js \
 && rm -rf package \
 && tar -xzf dat.gui-0.7.9.tgz \
 && cp package/build/dat.gui.min.js /3D-Global-Flight-Tracker/vendor/datgui-lite/dat.gui.min.js \
 && rm -rf package \
 && tar -xzf stats.js-0.17.0.tgz \
 && cp package/build/stats.min.js /3D-Global-Flight-Tracker/vendor/stats-min/stats.min.js \
 && rm -rf package ./*.tgz

# Local world-topology JPEG for the Earth texture: the verifier renders under
# net lockdown, so the texture must be a local file. Generated deterministically
# (stylised placeholder, not real imagery).
RUN pip3 install --no-cache-dir --break-system-packages pillow \
 && mkdir -p /3D-Global-Flight-Tracker/assets \
 && python3 -c "import random; from PIL import Image, ImageDraw; random.seed(20260918); W,H=2048,1024; img=Image.new('RGB',(W,H),(13,42,92)); d=ImageDraw.Draw(img); [d.ellipse([cx-rx,cy-ry,cx+rx,cy+ry], fill=(34,96,52)) for cx,cy,rx,ry in [(random.randint(0,W),random.randint(50,H-50),random.randint(20,180),random.randint(10,90)) for _ in range(220)]]; d.rectangle([0,0,W,40], fill=(230,235,240)); d.rectangle([0,H-40,W,H], fill=(230,235,240)); img.save('/3D-Global-Flight-Tracker/assets/world-topology.jpg', quality=88)"
