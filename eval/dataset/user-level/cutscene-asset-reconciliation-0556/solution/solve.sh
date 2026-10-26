#!/usr/bin/env bash
set -euo pipefail
cd /app

cat > /app/solution.py <<'PY'
import argparse
import json
import os

MANAGED_EXTS = (".playable", ".prefab")


def load_handauthored(assets_root):
    path = os.path.join(assets_root, ".handauthored")
    result = set()
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    result.add(line)
    return result


def list_managed(assets_root):
    files = set()
    for dirpath, _dirnames, filenames in os.walk(assets_root):
        for name in filenames:
            if name.endswith(MANAGED_EXTS):
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, assets_root).replace(os.sep, "/")
                files.add(rel)
    return files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--assets", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)
    entries = config.get("entries", [])
    assets_root = args.assets

    id_counts = {}
    for entry in entries:
        eid = entry.get("id")
        id_counts[eid] = id_counts.get(eid, 0) + 1

    invalid = set()
    valid_enabled = []
    for entry in entries:
        eid = entry.get("id")
        reasons = []
        if id_counts.get(eid, 0) > 1:
            reasons.append("duplicate_id")
        if not str(entry.get("key", "")).startswith("cutscene_"):
            reasons.append("bad_key")
        if entry.get("use_timeline") and entry.get("use_cutscene"):
            reasons.append("mutex_flags")
        if reasons:
            for reason in reasons:
                invalid.add((eid, reason))
        elif entry.get("enabled"):
            valid_enabled.append(entry)

    desired = set()
    for entry in valid_enabled:
        for path in entry.get("required", []):
            desired.add(path)

    handauthored = load_handauthored(assets_root)
    managed = list_managed(assets_root)

    kept = sorted(p for p in managed if p in desired)
    deleted = sorted(
        p for p in managed if p not in desired and p not in handauthored
    )

    missing = []
    for entry in valid_enabled:
        for path in entry.get("required", []):
            if path not in managed:
                missing.append({"id": entry.get("id"), "path": path})
    missing.sort(key=lambda r: (r["id"], r["path"]))

    invalid_list = sorted(
        ({"id": i, "reason": r} for (i, r) in invalid),
        key=lambda r: (r["id"], r["reason"]),
    )

    for path in deleted:
        full = os.path.join(assets_root, path)
        if os.path.isfile(full):
            os.remove(full)

    result = {
        "kept": kept,
        "deleted": deleted,
        "missing": missing,
        "invalid": invalid_list,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
PY

python /app/solution.py --config inputs/data/config.json --assets inputs/data/assets --out result.json
