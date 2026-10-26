#!/usr/bin/env python3
# legacy_analyzer.py — first-pass prototype from a summer intern (2024-08).
# Not maintained. Known to be incomplete. Kept around for reference only.
#
# TODO(intern): move regexes into a config file
# TODO(intern): support subdirectory pipelines/
# TODO(intern): consolidate duplicated glob logic
# TODO(intern): switch to argparse
# TODO(intern): stop parsing yaml with regex — use PyYAML

import os
import re
import sys


def find_pipelines():
    hits = []
    for name in os.listdir("/app/pipelines"):
        if name.endswith(".yml"):
            hits.append(os.path.join("/app/pipelines", name))
    return hits


def check_pinned_ref(text):
    # very rough — just look for @main / @latest / @dev in `uses:` lines
    bad = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("- uses:"):
            for suffix in ("@main", "@latest", "@dev"):
                if line.endswith(suffix):
                    bad.append(line)
                    break
    return bad


def check_test_step(text):
    return "test" in text.lower()


def dump_flag(name, ok):
    marker = "OK" if ok else "FAIL"
    print(f"[{marker}] {name}")


def main():
    files = find_pipelines()
    if not files:
        print("no pipelines found", file=sys.stderr)
        return 1
    for path in files:
        with open(path) as f:
            text = f.read()
        bad_refs = check_pinned_ref(text)
        has_tests = check_test_step(text)
        dump_flag(f"{os.path.basename(path)}:pinned_refs", not bad_refs)
        dump_flag(f"{os.path.basename(path)}:has_tests", has_tests)
    # TODO(intern): emit JSON report instead of stdout markers
    return 0


if __name__ == "__main__":
    sys.exit(main())
