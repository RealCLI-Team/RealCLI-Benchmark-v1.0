# task-level-clean-docker

`task-level-v0.1` 的精简版（115 MiB，原 ~1.5 GiB）。来自 GitHub 上游的代码仓库目录
（`<task>/environment/repository/`）不再随包携带，改为：

1. **每个任务的 `environment/Dockerfile` 里直接记录 repo URL + commit，并在构建时从
   GitHub 克隆**（build context 里不需要 `repository/`，`docker build` 开箱即用）：
   - 32 个任务用浅克隆取指定 commit，checkout 后删除 `.git`（与原快照一致）；
   - 8 个任务的评测依赖仓库 git 历史（评测前跑 `git checkout HEAD -- .`），用完整
     `git clone` 并保留 `.git`。
2. 每个任务旁另有 `repository.source.json`（repo / URL / commit / 逐字节校验结论）。

如不想在构建时联网克隆（例如本地离线检查），可在数据集根目录先用 `clone_repos.py`
把仓库物化回 `environment/repository/`：

```bash
python3 clone_repos.py                 # 克隆全部缺失仓库（默认 4 并发）
python3 clone_repos.py <task> [<task>] # 只克隆指定任务
python3 clone_repos.py --dry-run       # 只打印将要做什么
python3 clone_repos.py --strip-git     # 克隆后删除 .git
```

> 注：Dockerfile 构建路径总是从 GitHub 拉取，不会读本地 `environment/repository/`；
> 物化目录仅用于离线查看/对照。

- 全局清单：`SOURCES.json`（40 个仓库 + 2 个保留的 okhttp 快照）
- 每个仓库的克隆方式（Dockerfile 与脚本一致）：
  `git init <dst> && git -C <dst> remote add origin <url> && git -C <dst> fetch --depth 1 origin <commit> && git -C <dst> checkout FETCH_HEAD`
  （需要完整历史的 8 个任务为 `git clone <url> <dst> && git -C <dst> checkout <commit>`）
- 两份 okhttp 快照（`environment/okhttp-real-repo-src.tar.gz`）是裁剪过的定制快照，克隆无法复现，
  文件保留在数据集里，来源同时记录在对应 Dockerfile 顶部注释和旁挂的 `*.tar.gz.source.json`。
- 40 个仓库的快照内容都已逐一与上游 `commit` 的 checkout 做过逐字节比对（结论写在各
  `repository.source.json` 的 `verification` 字段）：37 个逐字节一致（sqlglot 仅差一个
  git 不跟踪的空 submodule 占位目录）；3 个（gradio / scipy / streamlit）快照裁剪了上游
  少量 dev/CI 文件、无任何内容差异（裁剪清单见 `snapshot_diff_vs_upstream`）。构建时
  clone 得到的是完整上游树（不含快照侧生成的 `*.pyi` 等文件）。
