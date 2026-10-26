FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-wqy-zenhei \
    fonts-wqy-microhei \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir \
    python-docx \
    openpyxl \
    Pillow \
    matplotlib \
    pytest \
    httpx \
    mammoth
COPY data/input.xlsx /workspace/data/input.xlsx
WORKDIR /workspace
