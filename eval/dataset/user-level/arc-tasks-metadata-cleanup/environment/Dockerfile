FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir --disable-pip-version-check \
    pandas==2.2.2 numpy==1.26.4 matplotlib==3.8.4

COPY inputs/ /app/inputs/

CMD ["/bin/bash"]
