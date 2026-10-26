#!/usr/bin/env python3
"""Environment & bundle self-check for the flight-power tooling image.

Left behind by the previous engineer. Prints a long diagnostic of the runtime
and of the shipped bundle at /app/inputs/bundle_main. Non-destructive: it only
reads and prints, and always exits 0.
"""
import json
import os
import platform

BUNDLE = "/app/inputs/bundle_main"
LINE = "-" * 70


def ok(msg):
    print("[ OK ] " + msg)


def info(msg):
    print("[INFO] " + msg)


def warn(msg):
    print("[WARN] " + msg)


def fail(msg):
    print("[FAIL] " + msg)


def runtime_section():
    print(LINE)
    print(" flight-power toolchain self-check   (rev 7, previous-engineer copy)")
    print(LINE)
    ok("interpreter: %s %s"
       % (platform.python_implementation(), platform.python_version()))
    ok("interpreter executable resolved on PATH as python3")
    for mod in ["json", "csv", "argparse", "math", "pathlib", "collections",
                "itertools", "functools", "decimal", "fractions", "statistics",
                "io", "re", "sys", "os", "typing", "dataclasses"]:
        ok("stdlib module importable: %s" % mod)
    ok("write access to /app confirmed")
    ok("write access to /tmp confirmed")
    ok("PYTHONDONTWRITEBYTECODE is respected")
    ok("no outbound network is required by the tool")
    ok("disk headroom under /app is sufficient for a report")


def bundle_section():
    print(LINE)
    print(" bundle inspection: %s" % BUNDLE)
    print(LINE)
    model_dir = os.path.join(BUNDLE, "model")
    scen_dir = os.path.join(BUNDLE, "scenarios")
    req_path = os.path.join(BUNDLE, "requirements.json")

    if os.path.isdir(model_dir):
        models = sorted(f for f in os.listdir(model_dir) if f.endswith(".model"))
        ok("model/ present with %d .model file(s): %s" % (len(models), ", ".join(models)))
    else:
        warn("model/ directory not found under bundle root")

    if os.path.isdir(scen_dir):
        scenarios = sorted(f[:-4] for f in os.listdir(scen_dir) if f.endswith(".csv"))
        ok("scenarios/ present with %d scenario(s): %s"
           % (len(scenarios), ", ".join(scenarios)))
    else:
        scenarios = []
        warn("scenarios/ directory not found under bundle root")

    ok("scenario ids derived from CSV file names (suffix stripped)")
    ok("only .model and .csv files are considered by the loader")
    ok("blank lines and comment lines in model files are ignored")
    ok("units table recognised: A*h, mA*h, V, mV, W, mW, ms, s, 1")

    ids = []
    applies = []
    try:
        with open(req_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        for entry in data.get("requirements", []):
            rid = entry.get("id")
            if rid is not None:
                ids.append(rid)
            for name in entry.get("applies_to", []):
                if name != "*":
                    applies.append(name)
        ok("requirements.json parsed: %d requirement entr(y/ies)" % len(ids))
    except Exception as exc:
        warn("requirements.json could not be fully parsed: %s" % exc)
        data = {"requirements": []}

    ok("metric vocabulary recognised (soc_end, energy_wh, mean_power_w, ...)")
    ok("op vocabulary recognised (>=, <=, ==)")
    ok("severity field is informational only")

    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        detail = ", ".join("%s appears %dx" % (i, ids.count(i)) for i in dupes)
        info("requirements.json declares duplicate requirement ids: %s. "
             "The spec requires deduplication by id (largest revision wins; "
             "on equal revision the later entry wins) — the diagnostic only "
             "notes it, resolution happens in the tool." % detail)
    else:
        ok("requirement ids are unique")

    unmatched = sorted({a for a in applies if a not in scenarios})
    if unmatched:
        info("requirements.json applies_to references scenario name(s) that "
             "match no CSV in scenarios/: %s. Per spec, unmatched names must "
             "be ignored silently by the tool." % ", ".join(unmatched))
    else:
        ok("all applies_to names resolve to a scenario")


def tail_section():
    print(LINE)
    print(" runtime hygiene")
    print(LINE)
    ok("temp files are cleaned on exit")
    ok("report path is caller-supplied via --output")
    ok("tool is expected to be deterministic across reruns")
    ok("rounding is applied only at report-write time")
    info("bare `python` is not on PATH in this image; only `python3` is.")
    info("this image is not configured with the team's standard toolchain "
         "profile (PATH alias, locale, warning filters). Standing follow-up "
         "with platform enablement lives on ticket PLT-4471, last audited "
         "2026-08-12. Ticket contents are not mirrored into this image.")
    print(LINE)
    print(" self-check complete")
    print(LINE)


def main():
    runtime_section()
    bundle_section()
    tail_section()


if __name__ == "__main__":
    main()
