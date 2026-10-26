# 离线工控机最小环境交付 runbook

本 runbook 描述无外网环境下构建可复现最小 chroot 并打包交付的步骤与产物约定。
所有操作只在当前工作目录内进行，软件源只允许 `inputs/local-rpm-repo/`。

## 输入

- `inputs/pkg-list.txt`：需直接安装的包清单（closure roots），忽略空行与 `#` 注释行。
- `inputs/local-rpm-repo/`：唯一离线软件源。
  - `repodata/packages.json`：仓库元数据，含每个包的 `name/version/release/arch/group/size/requires/payload/payload_sha256/sigfp`。
  - `*.rpkg`：每个包的有效载荷（gzip 压缩的 tar，解开后即为该包在根文件系统下的文件）。
- `inputs/RPM-GPG-KEY-local`：本地仓库签名信任锚，其 `Fingerprint:` 行给出受信任指纹。
- `inputs/rootfs-skeleton.tar.gz`：最小根骨架（FHS 目录与少量基础文件）。

## 依赖解析与安装规则

1. 先把 `rootfs-skeleton.tar.gz` 解压到 chroot 根目录。
2. 从 `pkg-list.txt` 出发，沿元数据 `requires` 递归求强依赖闭包，得到完整安装集合。
3. **签名门禁**：只有签名指纹与信任锚一致的包才允许进入安装集合。
   指纹为空（未签名 / NOKEY）或与信任锚不一致的包必须拒绝；若其落在闭包内则视为交付失败。
4. 安装即把每个被选中包的 `.rpkg` 载荷解开覆盖到 chroot 根目录。安装前应核对
   载荷文件的 sha256 与元数据 `payload_sha256` 一致。
5. 不得安装清单与闭包之外的任何包（保持最小化）。

## 产物（全部写入 `outputs/`）

- `outputs/chroot/`：构建好的最小根文件系统目录。
- `outputs/manifest.json`：物料清单，结构见 `inputs/docs/manifest-schema.md`。
- `outputs/checksums.sha256`：对 `chroot/` 下**所有普通文件**递归计算的 sha256。
  - 使用 `sha256sum` 标准两段式格式（`<hash>␠␠<path>`）。
  - 路径以 `chroot/` 为前缀的相对路径表示（相对任务根目录）。
  - 不得跳过零字节文件。
  - 按路径升序排列。
- `outputs/offline-chroot.tar.gz`：对 `chroot/` 目录打包的归档；在隔离环境解开后
  应能直接 `chroot . /bin/bash -c 'echo OK'` 成功。
- `outputs/rpm-signature-report.txt`：逐包签名核验报告，每行一条，格式：
  `<nevra> <status>`，其中 `status` 仅取：
  - `gpg OK`：指纹与信任锚一致；
  - `NOKEY`：包未签名（指纹为空）；
  - `FAILED`：指纹与信任锚不一致。
  报告需覆盖仓库中**全部**包（含未被安装的），按 `nevra` 升序排列。
  最终被安装进 chroot 的包必须全部为 `gpg OK`。
