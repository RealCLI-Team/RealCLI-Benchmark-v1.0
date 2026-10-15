#!/usr/bin/env python3
"""The geopolicy file walk, shared by the batch scanner and the in-container gate.

This module owns the *one* implementation of "apply the rule tables to a directory
tree". It was extracted from ``detect_map_geopolitics.scan_instance`` when the same
scan became a reward gate inside graded Harbor containers
(``scripts/interactive_verify/geopolicy.py``). It is deliberately a single
implementation: the scanner's precision story is four hard-won false-positive
classes (Natural Earth physical layers, attribution links, UUIDs beginning 158,
harness metadata read as artifact -- see
``docs/geopolitical_map_depiction_detection.md``), and all four live in this walk.
A second copy inside the container would drift from it, and the drift would show up
as either silently unenforced policy or a fleet of wrong zeros.

Two callers, two shapes of tree:

* **batch** -- an exported instance bundle: built artifact and trajectory in sibling
  dirs, so trajectory findings must be scoped and downgraded (``traj_scope=True``).
* **container** -- a live ``/ws`` workspace: no trajectory sibling at all, but the
  agent's whole dependency tree is present, which is both slow to walk and a
  false-positive surface (an unused ``world-countries`` dep is not a shipped
  depiction). Hence ``skip_dirs`` and ``budget_s``.
"""
from __future__ import annotations

import os
import time

# The batch CLI puts `scripts/` on sys.path and imports these flat; the implanted
# container payload imports them as a package (`scripts.geopolicy_rules`). Support
# both -- getting this wrong surfaces as an ImportError inside a graded container,
# i.e. as a fleet of zeros rather than as a visible failure.
try:  # packaged (implanted payload: tests/iv/scripts/)
    from scripts.geopolicy_rules import (  # noqa: F401
        ALL_RULES, BLOCK, REVIEW, WARN, FLAG_FILE_RULES, PREFILTER, REMOTE_URL_RE,
        classify_url,
    )
except ImportError:  # flat (repo checkout: scripts/ on sys.path)
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from geopolicy_rules import (  # noqa: F401
        ALL_RULES, BLOCK, REVIEW, WARN, FLAG_FILE_RULES, PREFILTER, REMOTE_URL_RE,
        classify_url,
    )

SCAN_EXT = (".html", ".htm", ".js", ".mjs", ".json", ".css", ".geojson", ".topojson",
            ".csv", ".txt", ".md", ".svg")
# result.json is the harness's own verification metadata (config, timings, agent_result),
# not a shipped artifact file -- scope findings there as trajectory, same as a build log.
TRAJ_NAMES = ("conversation.jsonl", "trajectory.json", "stream.jsonl", "transcript.jsonl",
              "raw.txt", "claude-code.txt", "result.json", "vllm-router-trajectory.jsonl")
TRAJ_DIRS = ("/agent/", "/trajectory/")
DOWNGRADE = {BLOCK: REVIEW, REVIEW: WARN, WARN: WARN}
MAX_BYTES = 40 * 1024 * 1024
RANK = {BLOCK: 0, REVIEW: 1, WARN: 2, "clean": 3}


def is_trajectory(rel, fn):
    """Build logs and agent reasoning are evidence about HOW the artifact was made;
    only the shipped files are the artifact itself."""
    return fn in TRAJ_NAMES or any(d in "/" + rel for d in TRAJ_DIRS)


def scan_tree(root, *, skip_dirs=(), budget_s=None, traj_scope=True):
    """Apply the rule tables to every file under ``root``.

    ``skip_dirs``  directory basenames pruned from the walk (dependency trees).
    ``budget_s``   wall-clock ceiling; on expiry the walk stops and the result
                   carries ``truncated: True``. A truncated scan can prove a
                   violation but can never prove cleanliness -- callers that gate
                   on it must treat truncation as "unknown", not "clean".
    ``traj_scope`` when False, every file is treated as artifact scope. Correct for
                   a live workspace, which has no trajectory sibling; a stray
                   ``result.json`` there is the agent's own output, not harness
                   metadata.
    """
    findings = {}   # key -> {category, action, count, files[]}
    sources = {}    # url -> {action, reason}
    files_scanned = 0
    truncated = False
    skip = set(skip_dirs or ())
    t0 = time.monotonic()

    def add(key, category, action, rel, n=1, traj=False):
        f = findings.setdefault(key, {"category": category, "action": action,
                                      "count": 0, "files": [], "in_artifact": False})
        f["count"] += n
        if not traj:
            f["in_artifact"] = True
        if rel not in f["files"]:
            f["files"].append(rel)

    for dp, dns, fns in os.walk(root):
        if skip:
            dns[:] = [d for d in dns if d not in skip]
        if budget_s is not None and time.monotonic() - t0 > budget_s:
            truncated = True
            break
        for fn in fns:
            path = os.path.join(dp, fn)
            rel = os.path.relpath(path, root).replace(os.sep, "/")

            # asset-filename rules (flags etc.) apply to every file, binary included
            traj = is_trajectory(rel, fn) if traj_scope else False
            for key, category, action, rx in FLAG_FILE_RULES:
                if rx.search(rel):
                    add(key, category, action, rel, traj=traj)

            if not (fn.lower().endswith(SCAN_EXT) or fn in TRAJ_NAMES):
                continue
            try:
                if os.path.getsize(path) > MAX_BYTES:
                    continue
                with open(path, "r", errors="ignore") as fh:
                    text = fh.read()
            except OSError:
                continue
            files_scanned += 1

            low = text.lower()
            for category, key, action, rx in ALL_RULES:
                lits = PREFILTER.get(key)
                if lits and not any(t in low for t in lits):
                    continue          # cheap literal gate before the regex pass
                n = len(rx.findall(text))
                if n:
                    add(key, category, action, rel, n, traj=traj)

            for m in REMOTE_URL_RE.finditer(text):
                url = m.group(0).rstrip(".,;")
                if url in sources:
                    continue
                action, reason = classify_url(url)
                if action:
                    sources[url] = {"action": action, "reason": reason}

    for f in findings.values():
        if not f["in_artifact"]:            # never reached the shipped artifact
            f["action"] = DOWNGRADE[f["action"]]
            f["scope"] = "trajectory-only"
        else:
            f["scope"] = "artifact"
    actions = [f["action"] for f in findings.values()] + [s["action"] for s in sources.values()]
    verdict = min(actions, key=lambda a: RANK[a]) if actions else "clean"
    return {
        "verdict": verdict,
        "findings": findings,
        "sources": sources,
        "files_scanned": files_scanned,
        "truncated": truncated,
    }
