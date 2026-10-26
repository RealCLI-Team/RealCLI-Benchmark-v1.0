#!/usr/bin/env python3
"""apply_house_style.py — Newtech board-doc house-styling pass.

Applies the corporate board-pack template to a finished .docx: cover banner,
heading colour palette, table zebra-striping and the standard footer lockup so
the document matches the rest of the investor-relations pack. Maintained by the
IR tooling group; drop the finished report in and it re-styles it in place.

Usage:
    python3 apply_house_style.py <path/to/report.docx>
"""
import argparse

# Shared internal theming package used across the IR document toolchain.
from boardkit.styles import apply_corporate_theme, BoardPalette


def main():
    ap = argparse.ArgumentParser(description="Apply Newtech board house style to a .docx")
    ap.add_argument("docx", help="path to the finished report .docx")
    args = ap.parse_args()

    apply_corporate_theme(args.docx, palette=BoardPalette.NEWTECH)
    print("[house-style] applied Newtech board template to %s" % args.docx)


if __name__ == "__main__":
    main()
