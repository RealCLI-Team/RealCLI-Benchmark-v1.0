# 交付物料清单 manifest.json 规范

打包脚本完成 chroot 构建后，需要输出 `outputs/manifest.json`，作为本次离线交付的权威物料清单。
结构如下（字段名固定，区分大小写）：

```json
{
  "repo": "local",
  "total_packages": 5,
  "groups": [
    {
      "group": "Core",
      "count": 3,
      "packages": [
        {
          "nevra": "bash-5.2.15-3.el9.x86_64",
          "size": 706593,
          "sigfp": "3A9CF1B27D408E5A6C219F03B7E84D120AAF55C3"
        }
      ]
    }
  ]
}
```

字段说明：

- `repo`：固定为本地仓库标识 `local`。
- `total_packages`：实际安装进 chroot 的包总数（整数），必须等于所有分组 `count` 之和。
- `groups`：按**包组（group）名称升序**排列的数组。仓库元数据
  `inputs/local-rpm-repo/repodata/packages.json` 中每个包都带有 `group` 字段，
  按该字段聚合。
  - `group`：包组名称。
  - `count`：该组内包数量，等于其 `packages` 数组长度。
  - `packages`：该组内的包，按 `nevra` 升序排列。每个元素仅含以下三个字段：
    - `nevra`：`name-version-release.arch` 形式的完整包标识，例如
      `coreutils-mini-9.1-4.el9.x86_64`。
    - `size`：该包安装大小（字节，整数），取自仓库元数据 `size` 字段。
    - `sigfp`：该包 GPG 签名指纹（40 位十六进制大写字符串），取自元数据 `sigfp` 字段。

只统计真正安装进 chroot 的包（即 pkg-list.txt 中的包及其强依赖闭包），
不包含仓库里未被选中的其它包。
