#!/usr/bin/env python3
"""deliv_scan — search the two deliverables for tokens.

Deliverables (.pptx / .xlsx) are zip containers; we scan their inner XML/text
parts as well as any plain-text output. Exit 0 only if every --present token is
found AND every --absent token is missing. Otherwise exit 1.

Usage:
  deliv_scan.py --absent TOKEN [--absent TOKEN ...]
  deliv_scan.py --present TOKEN [--absent TOKEN ...]
"""
import argparse
import glob
import os
import sys
import zipfile

OUT_DIR = os.environ.get("MODEL_OUTPUT_DIR", "/workspace/model_output")


def _iter_blobs():
    for path in sorted(glob.glob(os.path.join(OUT_DIR, "*"))):
        if not os.path.isfile(path):
            continue
        if path.lower().endswith((".pptx", ".xlsx", ".docx", ".zip")):
            try:
                with zipfile.ZipFile(path) as zf:
                    for name in zf.namelist():
                        try:
                            yield zf.read(name).decode("utf-8", "ignore")
                        except Exception:
                            continue
            except Exception:
                continue
        else:
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    yield fh.read()
            except Exception:
                continue


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--present", action="append", default=[])
    ap.add_argument("--absent", action="append", default=[])
    args = ap.parse_args()

    haystack = "\n".join(_iter_blobs())
    hay_lower = haystack.lower()

    for tok in args.present:
        if tok.lower() not in hay_lower:
            print(f"MISSING (expected present): {tok}")
            return 1
    for tok in args.absent:
        if tok.lower() in hay_lower:
            print(f"FOUND (expected absent): {tok}")
            return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
