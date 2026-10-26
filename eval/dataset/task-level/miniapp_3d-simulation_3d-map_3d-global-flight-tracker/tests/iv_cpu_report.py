#!/usr/bin/env python3
"""Report how much CPU the container was actually given, and whether the
browser was sized for it.

Chromium sizes every thread pool it owns -- above all SwiftShader's, the
software rasteriser that IS the cost of a headless 3D artifact -- from
``SysInfo::NumberOfProcessors()``. That reads the process affinity mask. It does
NOT read ``cpu.max``. So a container created with ``--cpus 8`` on a 192-core
host runs a browser built for 192 cores against 8 cores of quota:
``navigator.hardwareConcurrency`` reports 192 and the browser starts 64 threads.

The browser is therefore NOT under-using its quota -- it oversubscribes it. On
the testbed's three.js artifact at ``--cpus 8`` the GPU process alone peaks at
638% CPU and the container draws 107 CPU-seconds in 19s of wall clock. What the
mismatch costs is efficiency and *consistency*: measured across 12 concurrent
containers, each ``--cpus 8``, quota-only versus an affinity mask equal to the
quota (``docker --cpuset-cpus``):

    metric                quota only          mask == quota
    fps                   1.75 (1.36-1.87)    1.85 (1.82-1.90)
    click latency         8.50s (7.9-10.6)    8.00s (7.9-8.2)
    CPU per task          125s                100s
    periods throttled     49%                 0%
    fps spread            22%                 4%
    click spread          24%                 4%

Same work for 20% less CPU, and -- the part that matters for a verifier -- the
spread across identical containers collapses. Run-to-run spread is exactly what
turns one artifact into two verdicts: a click costing 10.6s in one run and 7.9s
in the next straddles a timeout, and a checklist item flips.

WHY THIS MODULE ONLY REPORTS, AND DOES NOT FIX
----------------------------------------------
The obvious fix -- ``os.sched_setaffinity`` to a quota-sized block at startup --
was implemented, measured, and REJECTED. It makes things much worse:

    12 concurrent, --cpus 8    fps                  click
    quota only (no pinning)    1.75 (1.36-1.87)     8.50s
    docker --cpuset-cpus       1.85 (1.82-1.90)     8.00s
    in-container setaffinity   0.75 (0.30-0.96)    23.50s   <-- 3x WORSE

The mechanism, found by diffing per-thread ``Cpus_allowed_list`` between the two:
SwiftShader's ``marl`` scheduler re-pins its own worker threads, and it pins
worker *i* to logical CPU *i* -- counting from zero, ignoring which CPUs our mask
actually contains. Under ``--cpuset-cpus 8-15`` every thread stays in 8-15
because the cgroup forbids anything else. Under an in-container mask of 8-15,
11 of the GPU process's threads come back with ``Cpus_allowed_list: 0-7``: they
escaped the mask, and every container on the host escapes to the SAME cores. A
fleet of 12 then contends for host CPUs 0-7 while holding 96 cores of quota,
which is why it measures three times slower than doing nothing.

Setting our own cpuset instead is not available either: ``/sys/fs/cgroup`` is
mounted read-only inside the container (cgroup2 ``ro,nosuid,nodev,noexec``), so
``cpuset.cpus`` cannot be written from in here.

Chromium flags do not help. Measured under the same 12-container load, none of
``--num-raster-threads=8``, ``--renderer-process-limit=1
--disable-features=site-per-process``, ``--in-process-gpu``,
``--disable-gpu-vsync --disable-frame-rate-limit``, or
``--disable-background-timer-throttling`` beat the baseline; the last two are
measurably worse.

So the mismatch is fixable ONLY where the container is created, by passing a
disjoint ``--cpuset-cpus`` block per container. That is a property of the fleet
runner (which alone knows what the other containers hold), not of this image and
not of this process. This module makes the cost visible in every run's metrics so
a fleet that can pass cpuset knows what it is buying.

One further trap for whoever implements that: the blocks must be disjoint in
PHYSICAL cores, not logical ids. This host is 96 physical cores with 2-way SMT
presented as 192 logical CPUs, and Linux enumerates siblings 96 apart
(``cpu0/topology/thread_siblings_list`` == "0,96"). Blocks of 8 consecutive
logical ids therefore pair up onto the same physical cores, and two SMT siblings
do not deliver two cores of a compute-bound rasteriser.
"""
from __future__ import annotations

import os
from pathlib import Path


def _quota_cpus() -> float | None:
    """CPU count from the cgroup quota, or None when unlimited/unreadable."""
    try:
        raw = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if raw[0] != "max":
            quota, period = int(raw[0]), int(raw[1])
            if quota > 0 and period > 0:
                return quota / period
        return None
    except Exception:  # noqa: BLE001 - cgroup v1, or no cgroup at all
        pass
    try:
        quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
        period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
        if quota > 0 and period > 0:
            return quota / period
    except Exception:  # noqa: BLE001
        pass
    return None


def _throttling() -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        for ln in Path("/sys/fs/cgroup/cpu.stat").read_text().splitlines():
            k, _, v = ln.partition(" ")
            if v.strip().isdigit():
                out[k] = int(v)
    except Exception:  # noqa: BLE001
        pass
    return out


def snapshot() -> dict:
    """Quota, visible CPUs, and the throttle counters, for metrics.json."""
    quota = _quota_cpus()
    try:
        visible = len(os.sched_getaffinity(0))
    except Exception:  # noqa: BLE001 - not Linux
        visible = os.cpu_count() or 0
    st = _throttling()
    return {
        "quota_cpus": quota,
        "visible_cpus": visible,
        # True when Chromium will size its pools for far more CPUs than the
        # container may use. Not an error -- see the module docstring -- but it
        # predicts throttling and run-to-run variance, so a verdict that looks
        # flaky can be checked against it instead of blamed on the artifact.
        "oversized_pools": bool(quota and visible > quota * 1.5),
        "nr_periods": st.get("nr_periods"),
        "nr_throttled": st.get("nr_throttled"),
        "throttled_usec": st.get("throttled_usec"),
    }


def delta(before: dict, after: dict) -> dict:
    """Throttling actually incurred between two snapshots."""
    out = dict(after)
    for k in ("nr_periods", "nr_throttled", "throttled_usec"):
        a, b = before.get(k), after.get(k)
        out[k] = (b - a) if isinstance(a, int) and isinstance(b, int) else b
    p, t = out.get("nr_periods") or 0, out.get("nr_throttled") or 0
    out["throttled_frac"] = round(t / p, 4) if p else None
    return out


def describe(snap: dict) -> str:
    """One log line."""
    q = snap.get("quota_cpus")
    q_s = f"{q:g}" if q else "unlimited"
    note = ""
    if snap.get("oversized_pools"):
        note = (f"  [browser thread pools sized for {snap['visible_cpus']} CPUs, "
                f"quota is {q_s} -- expect throttling; fix is a per-container "
                f"--cpuset-cpus at the runner, NOT in here]")
    frac = snap.get("throttled_frac")
    thr = f"  throttled={frac * 100:.0f}% of periods" if frac is not None else ""
    return f"cpu: quota={q_s} visible={snap.get('visible_cpus')}{thr}{note}"


if __name__ == "__main__":
    s = snapshot()
    print(describe(s))
    print(s)
