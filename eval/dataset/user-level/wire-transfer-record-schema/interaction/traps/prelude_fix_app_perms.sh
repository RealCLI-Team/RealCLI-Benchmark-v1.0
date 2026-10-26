# 权限修复（2026-09-17）：镜像 /app 为 root 2755，sandbox 模式下 agent 以
# uid=1000 运行，无法在 /app 创建判分要求的 transfer_record_schema.json
# （docker 模式 agent=root 无此问题）。start.sh 以 root 执行，这里放开 /app。
chmod 0777 /app
