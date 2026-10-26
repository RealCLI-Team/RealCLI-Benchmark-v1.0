# 2026-09-18：verifier 的 tracked-file 还原依赖 /app 是 git 仓库
# （git checkout HEAD -- .），镜像里 /app 不是 repo 导致还原失效（exit 128）。
# trial 启动时在 /app 建基线提交（本脚本由 start.sh 以 root 在 payload 种植前执行）。
if [ -d /app ] && [ ! -d /app/.git ]; then
  git -C /app init -q
  git -C /app add -A 2>/dev/null || true
  git -C /app -c user.email=clibench@local -c user.name=clibench commit -qm baseline 2>/dev/null || true
fi
