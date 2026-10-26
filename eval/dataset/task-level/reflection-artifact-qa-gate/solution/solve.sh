#!/usr/bin/env bash
set -euo pipefail

cat > /app/solution.py <<'PYEOF'
import argparse, json, os, sys, glob


def unquote(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def parse_yaml_ish(path):
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    data = {}
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if line.strip() == "" or line.lstrip().startswith("#"):
            i += 1
            continue
        if line[0] == " ":
            i += 1
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        rest = rest.strip()
        if rest == "|":
            i += 1
            block = []
            while i < n:
                l = lines[i]
                if l.strip() == "":
                    block.append("")
                    i += 1
                elif l.startswith("  "):
                    block.append(l[2:])
                    i += 1
                else:
                    break
            data[key] = "\n".join(block)
            continue
        if rest == "" or rest == "[]":
            if rest == "[]":
                data[key] = []
                i += 1
                continue
            j = i + 1
            items = []
            is_list = False
            while j < n:
                l = lines[j]
                if l.strip() == "" or l.lstrip().startswith("#"):
                    j += 1
                    continue
                if not l.startswith("  "):
                    break
                stripped = l.strip()
                if stripped.startswith("- "):
                    is_list = True
                    content = stripped[2:].strip()
                    if ":" in content and content[:1] not in ('"', "'"):
                        d = {}
                        k2, _, v2 = content.partition(":")
                        d[k2.strip()] = unquote(v2)
                        j += 1
                        while j < n:
                            l2 = lines[j]
                            if l2.strip() == "":
                                j += 1
                                continue
                            if l2.startswith("    ") and not l2.strip().startswith("- "):
                                k3, _, v3 = l2.strip().partition(":")
                                d[k3.strip()] = unquote(v3)
                                j += 1
                            else:
                                break
                        items.append(d)
                    else:
                        items.append(unquote(content))
                        j += 1
                else:
                    break
            data[key] = items if is_list else ""
            i = j
            continue
        data[key] = unquote(rest)
        i += 1
    return data


def finding(rule_id, status, detail):
    return {"rule_id": rule_id, "status": status, "detail": detail}


def evaluate(artifact_id, artifact, spec, constraints):
    findings = []
    min_chars = int(constraints["min_essence_chars"])
    amin = int(constraints["abandoned_min"])
    amax = int(constraints["abandoned_max"])

    ess_raw = artifact.get("unique_essence")
    ess = ess_raw.strip() if isinstance(ess_raw, str) else None
    has_ess = ess is not None and ess != ""

    ev = artifact.get("meta_reflection_evidence")
    is_list = isinstance(ev, list)
    n_items = len(ev) if is_list else 0

    # essence_present
    if ess_raw is None:
        findings.append(finding("essence_present", "fail", "unique_essence field is absent"))
    elif not has_ess:
        findings.append(finding("essence_present", "fail", "unique_essence is present but empty after trimming"))
    else:
        findings.append(finding("essence_present", "pass", "unique_essence present with %d code points" % len(ess)))

    # essence_length
    if not has_ess:
        findings.append(finding("essence_length", "fail", "cannot measure length: unique_essence absent or empty"))
    else:
        ln = len(ess)
        if ln >= min_chars:
            findings.append(finding("essence_length", "pass", "length %d >= %d" % (ln, min_chars)))
        else:
            findings.append(finding("essence_length", "fail", "length %d < %d" % (ln, min_chars)))

    # reflection_present
    if ev is None:
        findings.append(finding("reflection_present", "fail", "meta_reflection_evidence field is absent"))
    elif not is_list:
        findings.append(finding("reflection_present", "fail", "meta_reflection_evidence is not a list"))
    elif n_items == 0:
        findings.append(finding("reflection_present", "fail", "meta_reflection_evidence is an empty list"))
    else:
        findings.append(finding("reflection_present", "pass", "meta_reflection_evidence present with %d items" % n_items))

    # reflection_count
    if not is_list:
        findings.append(finding("reflection_count", "fail", "cannot count: meta_reflection_evidence absent or not a list"))
    else:
        if amin <= n_items <= amax:
            findings.append(finding("reflection_count", "pass", "%d items within [%d, %d]" % (n_items, amin, amax)))
        else:
            findings.append(finding("reflection_count", "fail", "%d items outside [%d, %d]" % (n_items, amin, amax)))

    # root_desires_covered
    root_desires = spec.get("root_desires") or []
    if not has_ess:
        findings.append(finding("root_desires_covered", "fail", "cannot check coverage: unique_essence absent or empty"))
    else:
        missing = [d for d in root_desires if d not in ess]
        if not missing:
            findings.append(finding("root_desires_covered", "pass", "all %d root desires present" % len(root_desires)))
        else:
            findings.append(finding("root_desires_covered", "fail", "missing: " + ", ".join(missing)))

    # no_forbidden_patterns
    text_parts = []
    if has_ess:
        text_parts.append(ess)
    if is_list:
        text_parts.extend(str(x) for x in ev)
    haystack = "\n".join(text_parts).lower()
    hits = []
    for p in (spec.get("forbidden_patterns") or []):
        phrase = str(p.get("phrase", "")).lower()
        if phrase and phrase in haystack:
            hits.append(str(p.get("id", "")))
    if not hits:
        findings.append(finding("no_forbidden_patterns", "pass", "no forbidden patterns present"))
    else:
        findings.append(finding("no_forbidden_patterns", "fail", "hit: " + ", ".join(hits)))

    findings.sort(key=lambda x: x["rule_id"])
    overall = "pass" if all(f["status"] == "pass" for f in findings) else "fail"
    return {"artifact_id": artifact_id, "overall": overall, "findings": findings}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    root = args.input
    constraints = parse_yaml_ish(os.path.join(root, "constraints.yaml"))

    # collect artifacts and detect duplicate stems
    art_files = sorted(glob.glob(os.path.join(root, "artifacts", "*.yaml")) +
                       glob.glob(os.path.join(root, "artifacts", "*.yml")))
    stem_map = {}
    for f in art_files:
        stem = os.path.splitext(os.path.basename(f))[0]
        stem_map.setdefault(stem, []).append(f)
    dups = sorted(s for s, fs in stem_map.items() if len(fs) > 1)
    if dups:
        sys.stderr.write("duplicate artifact_id(s): %s\n" % ", ".join(dups))
        sys.exit(3)

    results = []
    for stem in sorted(stem_map):
        artifact = parse_yaml_ish(stem_map[stem][0])
        spec_path = None
        for ext in (".yaml", ".yml"):
            cand = os.path.join(root, "specs", stem + ext)
            if os.path.exists(cand):
                spec_path = cand
                break
        if spec_path is None:
            results.append({"artifact_id": stem, "overall": "fail",
                            "findings": [finding("spec_found", "fail",
                                                 "no spec file found for artifact_id '%s'" % stem)]})
            continue
        spec = parse_yaml_ish(spec_path)
        results.append(evaluate(stem, artifact, spec, constraints))

    results.sort(key=lambda x: x["artifact_id"])
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({"artifacts": results}, f, ensure_ascii=False, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
PYEOF

python3 /app/solution.py --input /app/inputs --output /app/result.json
