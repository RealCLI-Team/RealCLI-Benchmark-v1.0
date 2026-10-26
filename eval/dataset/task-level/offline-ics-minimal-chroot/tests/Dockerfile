# Verifier image. Mirrors environment/Dockerfile so the separate verifier
# container has the same runtime as the agent image. At verify time /app and
# /workspace are re-materialized from the agent artifacts; this directory is
# also baked into /tests and re-uploaded before verification.
FROM ubuntu:24.04

# 离线任务运行环境：仅需 bash/coreutils/tar/gzip/jq/python3。
# 不联网安装额外软件；构建时如需 apt 由镜像基础层保证基本工具，已包含的不再安装。
ENV DEBIAN_FRONTEND=noninteractive

# jq 与 python3 用于元数据解析与 JSON 生成；其余为基础镜像自带。
RUN apt-get update \
 && apt-get install -y --no-install-recommends jq python3 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 仅复制运行期所需的输入材料（离线软件源、清单、骨架、契约文档）。
COPY inputs/ /app/inputs/

CMD ["/bin/bash"]

# Bake the verifier scripts into the image (harbor re-uploads them at verify time).
COPY . /tests
