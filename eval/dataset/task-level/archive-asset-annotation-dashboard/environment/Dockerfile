FROM python:3.12-slim

# 离线数据处理所需的常见 CLI 工具
RUN apt-get update \
    && apt-get install -y --no-install-recommends unzip coreutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 仅复制运行任务所需的输入材料
COPY inputs/ /app/inputs/

CMD ["bash"]
