FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-calc && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir openpyxl pytest tqdm
COPY data/input.xlsx /workspace/data/input.xlsx
WORKDIR /workspace
