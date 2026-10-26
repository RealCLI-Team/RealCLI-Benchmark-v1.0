#!/bin/bash
set -e

cat << 'PYEOF' > /app/analyze_pipelines.py
import os
import json
import re
import yaml


def parse_workflow(filepath):
    with open(filepath, "r") as f:
        return yaml.safe_load(f)


def get_all_jobs(doc):
    return doc.get("jobs", {})


def get_all_steps(job):
    return job.get("steps", [])


def check_r1(doc, job_name, steps):
    """All action refs must use pinned SHA or semver tag."""
    violations = []
    for i, step in enumerate(steps):
        uses = step.get("uses", "")
        if uses:
            action_ref = uses.split("@")[-1] if "@" in uses else ""
            if not action_ref:
                continue
            is_sha = bool(re.fullmatch(r"[0-9a-f]{40}", action_ref))
            is_semver = bool(re.fullmatch(r"v?\d+\.\d+\.\d+", action_ref))
            if not is_sha and not is_semver:
                violations.append({
                    "rule": "R1",
                    "detail": f"Unpinned action ref: {uses}",
                    "job": job_name,
                    "step": i,
                })
    return violations


def check_r2(doc, job_name, steps):
    """Must have a test step."""
    for step in steps:
        name = step.get("name", "").lower()
        run = step.get("run", "").lower()
        if "test" in name or "test" in run:
            return []
    return [{"rule": "R2", "detail": "No test step found", "job": job_name, "step": -1}]


def check_r3(doc, job_name, job_def):
    """Prod deploy must depend on staging."""
    name_lower = job_name.lower()
    if "prod" in name_lower and "deploy" in name_lower:
        needs = job_def.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        has_staging = any("staging" in n.lower() for n in needs)
        if not has_staging:
            return [{"rule": "R3", "detail": "Production deploy without staging dependency", "job": job_name, "step": -1}]
    return []


def check_r4(doc, job_name, steps):
    """No hardcoded secrets."""
    violations = []
    secret_patterns = re.compile(r"(password|secret|token|api[_-]?key|access[_-]?key|aws[_-]?secret)", re.IGNORECASE)
    for i, step in enumerate(steps):
        env = step.get("env", {})
        if not env:
            continue
        for key, value in env.items():
            if not isinstance(value, str):
                continue
            if secret_patterns.search(key) and not value.startswith("${{"):
                violations.append({
                    "rule": "R4",
                    "detail": f"Hardcoded secret found: {key}",
                    "job": job_name,
                    "step": i,
                })
    return violations


def check_r5(doc, job_name, steps):
    """Must have notification step."""
    for step in steps:
        name = step.get("name", "").lower()
        run = step.get("run", "").lower()
        if any(kw in name or kw in run for kw in ["notify", "slack", "email", "teams", "webhook"]):
            return []
    return [{"rule": "R5", "detail": "No notification step found", "job": job_name, "step": -1}]


def check_r6(doc, job_name, job_def):
    """Deploy jobs must have resource limits."""
    name_lower = job_name.lower()
    if "deploy" not in name_lower:
        return []

    container = job_def.get("container", {})
    if isinstance(container, dict):
        options = container.get("options", "")
        if "memory" in options or "cpus" in options:
            return []

    return [{"rule": "R6", "detail": "No resource limits on deploy job", "job": job_name, "step": -1}]


def check_r7(doc):
    """Concurrency must be defined."""
    if doc.get("concurrency"):
        return []
    jobs = get_all_jobs(doc)
    for job_def in jobs.values():
        if isinstance(job_def, dict) and job_def.get("concurrency"):
            return []
    return [{"rule": "R7", "detail": "No concurrency group defined", "job": "-", "step": -1}]


def check_r8(doc):
    """Permissions must be explicitly set."""
    if doc.get("permissions"):
        return []
    jobs = get_all_jobs(doc)
    for job_def in jobs.values():
        if isinstance(job_def, dict) and job_def.get("permissions"):
            return []
    return [{"rule": "R8", "detail": "No explicit permissions defined", "job": "-", "step": -1}]


def analyze_workflow(filepath):
    doc = parse_workflow(filepath)
    name = doc.get("name", os.path.basename(filepath))
    jobs = get_all_jobs(doc)

    all_violations = []

    for job_name, job_def in jobs.items():
        if not isinstance(job_def, dict):
            continue
        steps = get_all_steps(job_def)

        all_violations.extend(check_r1(doc, job_name, steps))
        all_violations.extend(check_r2(doc, job_name, steps))
        all_violations.extend(check_r3(doc, job_name, job_def))
        all_violations.extend(check_r4(doc, job_name, steps))
        all_violations.extend(check_r5(doc, job_name, steps))
        all_violations.extend(check_r6(doc, job_name, job_def))

    all_violations.extend(check_r7(doc))
    all_violations.extend(check_r8(doc))

    violated_rules = set(v["rule"] for v in all_violations)
    score = int(100 - 12.5 * len(violated_rules))
    score = max(0, score)

    has_critical = "R3" in violated_rules or "R4" in violated_rules
    safe = score >= 75 and not has_critical

    return {
        "safe": safe,
        "score": score,
        "violations": all_violations,
    }


def main():
    pipeline_dir = "/app/pipelines"
    report = {"pipelines": {}}

    for fname in sorted(os.listdir(pipeline_dir)):
        if fname.endswith(".yml") or fname.endswith(".yaml"):
            fpath = os.path.join(pipeline_dir, fname)
            result = analyze_workflow(fpath)
            name = parse_workflow(fpath).get("name", fname)
            report["pipelines"][name] = result

    with open("/app/report.json", "w") as f:
        json.dump(report, f, indent=2)

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
PYEOF

cd /app && python analyze_pipelines.py
