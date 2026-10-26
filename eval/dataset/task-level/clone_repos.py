#!/usr/bin/env python3
"""Materialize the GitHub-sourced repository directories of this cleaned dataset.

Repositories that used to live at <task>/environment/repository/ are not shipped
here; they are described by <task>/environment/repository.source.json
(repo + URL + commit) and listed in SOURCES.json.

Usage (run at the dataset root):

    python3 clone_repos.py                     # clone everything that is missing
    python3 clone_repos.py <task> [<task>...]  # only the given tasks
    python3 clone_repos.py --dry-run           # show what would be cloned
    python3 clone_repos.py --jobs 8            # parallel clones (default 4)
    python3 clone_repos.py --strip-git         # drop .git after checkout

Each clone fetches exactly the recorded commit (no history, no full clone):

    git init <dst> && git -C <dst> remote add origin <url> && \
    git -C <dst> fetch --depth 1 origin <commit> && git -C <dst> checkout FETCH_HEAD

The .git directory is kept by default (useful for git/setuptools-scm based version
resolution); pass --strip-git to remove it, as most original snapshots had none.
"""
import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def clone_one(e, dry, strip_git):
    dst = os.path.join(HERE, e["task"], e["target"])
    tag = f"{e['task']}"
    if os.path.isdir(dst) and os.listdir(dst):
        return tag, "skip", f"{e['target']} already exists"
    if dry:
        return tag, "dry", f"would clone {e['repo']}@{e['commit_short']} -> {e['target']}"
    os.makedirs(dst, exist_ok=True)
    cmds = [
        ["git", "init", "-q", dst],
        ["git", "-C", dst, "remote", "add", "origin", e["url"]],
        ["git", "-C", dst, "fetch", "-q", "--depth", "1", "origin", e["commit"]],
        ["git", "-C", dst, "checkout", "-q", "FETCH_HEAD"],
    ]
    for c in cmds:
        r = subprocess.run(c, capture_output=True, text=True)
        if r.returncode:
            return tag, "fail", f"{' '.join(c)} -> {r.stderr.strip()[:200]}"
    if strip_git:
        shutil.rmtree(os.path.join(dst, ".git"), ignore_errors=True)
    return tag, "ok", f"{e['repo']}@{e['commit_short']}"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tasks", nargs="*", help="task names (default: all)")
    ap.add_argument("--jobs", type=int, default=4, help="parallel clones (default 4)")
    ap.add_argument("--dry-run", action="store_true", help="only print what would be done")
    ap.add_argument("--strip-git", action="store_true",
                    help="remove the .git directory after checkout "
                         "(most original snapshots had no .git; keep it if the build "
                         "resolves versions via git/setuptools-scm)")
    args = ap.parse_args()

    manifest = json.load(open(os.path.join(HERE, "SOURCES.json")))
    repos = manifest["repos"]
    if args.tasks:
        want = set(args.tasks)
        repos = [e for e in repos if e["task"] in want]
        unknown = want - {e["task"] for e in repos}
        if unknown:
            sys.exit(f"unknown task(s): {', '.join(sorted(unknown))}")

    counts = {"ok": 0, "skip": 0, "fail": 0, "dry": 0}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        futs = [ex.submit(clone_one, e, args.dry_run, args.strip_git) for e in repos]
        for f in concurrent.futures.as_completed(futs):
            tag, status, msg = f.result()
            counts[status] = counts.get(status, 0) + 1
            print(f"[{status:4s}] {tag:52s} {msg}", flush=True)

    print(f"\ndone: ok={counts['ok']} skip={counts['skip']} fail={counts['fail']}"
          + (f" dry={counts['dry']}" if args.dry_run else ""))
    if counts["fail"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
