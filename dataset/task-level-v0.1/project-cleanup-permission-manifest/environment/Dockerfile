FROM python:3.13-slim-bookworm

WORKDIR /app
COPY generate_corpus.py /app/generate_corpus.py
RUN python3 /app/generate_corpus.py && rm -f /app/generate_corpus.py
CMD ["/bin/bash"]
