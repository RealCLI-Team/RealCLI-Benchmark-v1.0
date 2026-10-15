#!/usr/bin/env python3
"""Render PPTX/HTML primary deliverables into verifier-private PNGs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], timeout: int = 300) -> None:
    completed = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
    if completed.returncode:
        raise RuntimeError(
            f"render command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout={completed.stdout[-2000:]}\nstderr={completed.stderr[-2000:]}"
        )


def binary(*names: str) -> str:
    for name in names:
        resolved = shutil.which(name)
        if resolved:
            return resolved
    raise RuntimeError(f"required render binary missing: {', '.join(names)}")


def render_pdf(pdf: Path, target: Path, prefix: str) -> list[Path]:
    pdftoppm = binary("pdftoppm")
    run([pdftoppm, "-png", "-r", "120", str(pdf), str(target / prefix)], timeout=600)
    images = sorted(target.glob(f"{prefix}-*.png"))
    if not images:
        raise RuntimeError(f"no pages rendered from {pdf}")
    return images


def render_pptx(primary: Path, target: Path) -> tuple[list[Path], dict]:
    office = binary("libreoffice", "soffice")
    with tempfile.TemporaryDirectory(prefix="jobbench-ppt-render-") as tmp:
        tmpdir = Path(tmp)
        # 独立 UserInstallation 防止并发 soffice 实例抢 profile 锁
        user_inst = tmpdir / "lo_profile"
        user_inst.mkdir()
        run([office, "--headless", f"-env:UserInstallation=file://{user_inst}",
             "--convert-to", "pdf", "--outdir", str(tmpdir), str(primary)], timeout=600)
        pdf = tmpdir / f"{primary.stem}.pdf"
        if not pdf.is_file():
            raise RuntimeError("LibreOffice did not produce the expected PDF")
        images = render_pdf(pdf, target, "slide")
    return images, {"kind": "pptx", "slide_count": len(images), "renderer": Path(office).name}


def chromium_binary() -> str:
    for name in ("chromium", "google-chrome", "google-chrome-stable", "chromium-browser"):
        resolved = shutil.which(name)
        if not resolved:
            continue
        health = subprocess.run(
            [resolved, "--version"], text=True, capture_output=True,
            timeout=20, check=False,
        )
        if health.returncode == 0 and (health.stdout or health.stderr).strip():
            return resolved
    if os.geteuid() != 0 or shutil.which("apt-get") is None:
        raise RuntimeError("Chromium is missing and cannot be installed by the verifier")
    run(["apt-get", "update", "-qq"], timeout=600)
    run(["apt-get", "install", "-y", "-qq", "--no-install-recommends", "chromium"], timeout=900)
    return binary("chromium")


def render_html(primary: Path, target: Path) -> tuple[list[Path], dict]:
    chrome = chromium_binary()
    uri = primary.resolve().as_uri()
    common = [chrome, "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--run-all-compositor-stages-before-draw", "--virtual-time-budget=5000"]
    desktop = target / "html-desktop.png"
    mobile = target / "html-mobile.png"
    pdf = target / "html-full.pdf"
    run(common + ["--window-size=1440,1000", f"--screenshot={desktop}", uri])
    run(common + ["--window-size=390,844", f"--screenshot={mobile}", uri])
    run(common + ["--window-size=1440,1000", f"--print-to-pdf={pdf}", "--no-pdf-header-footer", uri])
    pages = render_pdf(pdf, target, "html-page")
    pdf.unlink(missing_ok=True)
    images = [desktop, mobile, *pages]
    return images, {"kind": "html", "desktop_viewport": [1440, 1000], "mobile_viewport": [390, 844], "page_tiles": len(pages), "renderer": Path(chrome).name}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--primary", type=Path, required=True)
    parser.add_argument("--render-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    if not args.primary.is_file():
        raise FileNotFoundError(args.primary)
    if args.render_dir.exists():
        shutil.rmtree(args.render_dir)
    args.render_dir.mkdir(parents=True)
    suffix = args.primary.suffix.lower()
    if suffix == ".pptx":
        images, metadata = render_pptx(args.primary, args.render_dir)
    elif suffix == ".html":
        images, metadata = render_html(args.primary, args.render_dir)
    else:
        raise ValueError(f"unsupported primary artifact for visual evaluation: {suffix}")
    payload = {
        "schema_version": "manual-hard6.render-manifest.v1",
        "primary": args.primary.name,
        "primary_sha256": sha256(args.primary),
        **metadata,
        "images": [
            {"path": path.name, "bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in images
        ],
    }
    args.manifest.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
