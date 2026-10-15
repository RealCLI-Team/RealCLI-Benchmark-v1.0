FROM python:3.12-slim

# Offline data-cleaning task. Only stdlib Python + sqlite3 + file/md5sum are used.
RUN apt-get update \
    && apt-get install -y --no-install-recommends file sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Only runtime input material is copied into the image.
COPY inputs/ /app/inputs/

CMD ["bash"]
