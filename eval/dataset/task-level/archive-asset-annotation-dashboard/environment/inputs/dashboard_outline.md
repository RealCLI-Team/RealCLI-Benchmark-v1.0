# 离线看板要点（dashboard_outline）

`dashboard.html` 必须是一个**完全离线**可打开的单文件页面：所有样式与脚本都内联，
不引用任何外网资源（无 http/https 的 CDN、远程 JS、远程图片或在线 API）。

页面至少包含：

1. 顶部概览：总文件数、重复文件数、异常文件数三个关键指标。
2. 文件类型分布图表：展示各 `type_tag` 的文件数量（可用纯 CSS/SVG 或内联 JS 绘制条形图，
   不依赖外部库）。
3. 异常文件明细表：列出所有 `status = anomaly` 的文件（含 file_path 与 size_bytes）。
