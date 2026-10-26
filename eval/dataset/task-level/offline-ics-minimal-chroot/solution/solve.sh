#!/usr/bin/env bash
# 离线工控机最小 chroot 构建与交付打包。
# 全程离线，软件源仅限 inputs/local-rpm-repo/，所有读写限于任务根目录。
set -euo pipefail

# 定位任务根目录（包含 inputs/ 的目录）。solution/ 可能被挂载到任意路径
# （如 /solution），因此不能依赖脚本自身位置；按已知候选路径探测。
ROOT=""
for cand in /app /task "$PWD"; do
  if [ -f "$cand/inputs/pkg-list.txt" ]; then ROOT="$cand"; break; fi
done
[ -n "$ROOT" ] || { echo "cannot locate task root with inputs/" >&2; exit 1; }
cd "$ROOT"

REPO="inputs/local-rpm-repo"
META="$REPO/repodata/packages.json"
KEY="inputs/RPM-GPG-KEY-local"
PKGLIST="inputs/pkg-list.txt"
SKEL="inputs/rootfs-skeleton.tar.gz"

OUT="outputs"
CHROOT="$OUT/chroot"

rm -rf "$OUT"
mkdir -p "$CHROOT"

# 受信任指纹（来自信任锚文件的 Fingerprint: 行）
TRUST_FP="$(grep -i '^Fingerprint:' "$KEY" | head -n1 | sed 's/.*: *//; s/[[:space:]]//g')"
[ -n "$TRUST_FP" ] || { echo "no trusted fingerprint" >&2; exit 1; }

# ---------- 1) 解开根骨架 ----------
tar -xzf "$SKEL" -C "$CHROOT"

# ---------- 2) 读取 closure roots ----------
mapfile -t ROOTS < <(grep -vE '^\s*(#|$)' "$PKGLIST" | sed 's/[[:space:]]//g' | grep -v '^$')

# ---------- 3) 递归求强依赖闭包，并施加签名门禁 ----------
# 工具函数：给定包名，返回元数据对象（取首个匹配）
pkg_obj () { jq -c --arg n "$1" '.packages[] | select(.name==$n)' "$META" | head -n1; }

declare -A SELECTED   # name -> nevra
declare -A SEEN
queue=("${ROOTS[@]}")

while [ "${#queue[@]}" -gt 0 ]; do
  name="${queue[0]}"; queue=("${queue[@]:1}")
  [ -n "${SEEN[$name]:-}" ] && continue
  SEEN[$name]=1
  obj="$(pkg_obj "$name")"
  [ -n "$obj" ] || { echo "package not found in repo: $name" >&2; exit 1; }

  sigfp="$(jq -r '.sigfp' <<<"$obj")"
  nevra="$(jq -r '"\(.name)-\(.version)-\(.release).\(.arch)"' <<<"$obj")"

  # 签名门禁：闭包内的包必须签名且与信任锚一致
  if [ -z "$sigfp" ] || [ "$sigfp" = "null" ]; then
    echo "refuse unsigned (NOKEY) package in closure: $nevra" >&2; exit 1
  fi
  if [ "$sigfp" != "$TRUST_FP" ]; then
    echo "refuse package with bad signature: $nevra" >&2; exit 1
  fi

  SELECTED[$name]="$nevra"

  while IFS= read -r dep; do
    [ -n "$dep" ] && [ -z "${SEEN[$dep]:-}" ] && queue+=("$dep")
  done < <(jq -r '.requires[]?' <<<"$obj")
done

# ---------- 4) 安装：校验载荷 sha256 后解开到 chroot ----------
for name in "${!SELECTED[@]}"; do
  obj="$(pkg_obj "$name")"
  payload="$(jq -r '.payload' <<<"$obj")"
  want="$(jq -r '.payload_sha256' <<<"$obj")"
  got="$(sha256sum "$REPO/$payload" | awk '{print $1}')"
  [ "$got" = "$want" ] || { echo "payload checksum mismatch: $payload" >&2; exit 1; }
  tar -xzf "$REPO/$payload" -C "$CHROOT"
done

# ---------- 5) manifest.json （按包组聚合） ----------
# 选中的 nevra 列表
SEL_NEVRAS="$(printf '%s\n' "${SELECTED[@]}" | sort)"
jq -n \
  --argjson sel "$(printf '%s\n' "${SELECTED[@]}" | jq -R . | jq -s .)" \
  --slurpfile meta "$META" '
  ($meta[0].packages
    | map(. + {nevra: "\(.name)-\(.version)-\(.release).\(.arch)"})
    | map(select(.nevra as $x | $sel | index($x)))) as $pkgs
  | ($pkgs | group_by(.group) | sort_by(.[0].group)) as $grps
  | {
      repo: "local",
      total_packages: ($pkgs | length),
      groups: ($grps | map({
        group: .[0].group,
        count: (. | length),
        packages: (sort_by(.nevra) | map({nevra, size, sigfp}))
      }))
    }
' > "$OUT/manifest.json"

# ---------- 6) checksums.sha256 （递归覆盖所有普通文件，含零字节） ----------
# 路径以 chroot/ 前缀的相对路径表示，按路径升序
( cd "$OUT" && find chroot -type f | LC_ALL=C sort | while IFS= read -r f; do
    sha256sum "$f"
  done ) > "$OUT/checksums.sha256"

# ---------- 7) signature report （覆盖仓库全部包） ----------
jq -r --arg trust "$TRUST_FP" '
  .packages
  | map({nevra: "\(.name)-\(.version)-\(.release).\(.arch)", sigfp})
  | sort_by(.nevra)[]
  | .nevra + " " + (
      if (.sigfp == null or .sigfp == "") then "NOKEY"
      elif (.sigfp == $trust) then "gpg OK"
      else "FAILED" end)
' "$META" > "$OUT/rpm-signature-report.txt"

# ---------- 8) 打包归档 ----------
( cd "$CHROOT" && tar --sort=name --owner=0 --group=0 --numeric-owner \
    --mtime='2024-01-01 00:00:00 UTC' -cf - . ) | gzip -n -9 > "$OUT/offline-chroot.tar.gz"

echo "done: $(jq -r '.total_packages' "$OUT/manifest.json") packages installed"
