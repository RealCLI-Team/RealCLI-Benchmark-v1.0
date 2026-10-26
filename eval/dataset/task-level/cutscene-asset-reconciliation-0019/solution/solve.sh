#!/usr/bin/env bash
set -euo pipefail
cd /app

cat > /app/solution.py <<'PY'
#!/usr/bin/env python3
import argparse, json, os


MANAGED_EXTS = (".playable", ".prefab")


def list_managed(assets_root):
    out = []
    for dirpath, _dirs, files in os.walk(assets_root):
        for name in files:
            if name.endswith(MANAGED_EXTS):
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, assets_root).replace(os.sep, "/")
                out.append(rel)
    return out


def load_handauthored(assets_root):
    path = os.path.join(assets_root, ".handauthored")
    keep = set()
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    keep.add(line)
    return keep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--assets", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    entries = cfg.get("entries", [])
    assets_root = args.assets

    id_counts = {}
    for e in entries:
        id_counts[e["id"]] = id_counts.get(e["id"], 0) + 1

    invalid_pairs = set()
    invalid_ids = set()
    for e in entries:
        reasons = []
        if e.get("use_timeline") and e.get("use_cutscene"):
            reasons.append("mutex_flags")
        if not str(e.get("key", "")).startswith("cutscene_"):
            reasons.append("bad_key")
        if id_counts[e["id"]] > 1:
            reasons.append("duplicate_id")
        if reasons:
            invalid_ids.add(e["id"])
            for r in reasons:
                invalid_pairs.add((e["id"], r))

    desired = set()
    missing = []
    for e in entries:
        if e["id"] in invalid_ids:
            continue
        if not e.get("enabled"):
            continue
        for rel in e.get("required", []):
            desired.add(rel)
            if not os.path.isfile(os.path.join(assets_root, rel)):
                missing.append({"id": e["id"], "path": rel})

    handauthored = load_handauthored(assets_root)

    kept = []
    deleted = []
    for rel in list_managed(assets_root):
        if rel in desired:
            kept.append(rel)
        elif rel in handauthored:
            continue
        else:
            os.remove(os.path.join(assets_root, rel))
            deleted.append(rel)

    result = {
        "kept": sorted(kept),
        "deleted": sorted(deleted),
        "missing": sorted(missing, key=lambda r: (r["id"], r["path"])),
        "invalid": [
            {"id": i, "reason": r}
            for (i, r) in sorted(invalid_pairs)
        ],
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, sort_keys=False)
        f.write("\n")


if __name__ == "__main__":
    main()
PY

python /app/solution.py --config /app/inputs/config.json --assets /app/inputs/assets --out /app/result.json
