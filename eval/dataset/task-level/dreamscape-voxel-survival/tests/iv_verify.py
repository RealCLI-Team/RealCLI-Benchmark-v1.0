#!/usr/bin/env python3
"""IN-CONTAINER entrypoint for the implanted interactive verifier.

This file is COPIED into every Harbor instance as ``tests/iv_verify.py`` by
``scripts/harbor_pipeline/implant_interactive_verify.py``. It runs inside the
task's own Docker image, after the agent has built its artifact, and turns the
workspace into a Harbor reward:

    workspace  -> pick entry HTML          (service.verify.htmleval_exec._pick_entry)
               -> generate a checklist     (gen LLM, harbor_loader.build_checklist)
               -> plan + drive + capture   (Playwright, pipeline.verify_task)
               -> judge items + aesthetics (VLM/text judges)
               -> reward.txt

WHY A SEPARATE DRIVER (and not ``run_harbor.py``)
─────────────────────────────────────────────────
``run_harbor.py`` loads a *distill-platform* instance: it expects the artifact
inside ``attempt-1/extracted/<id>/verifier/extra_saved_production.tar.gz`` and
recovers the build prompt from ``attempt-1/conversation.jsonl``. Neither exists
here — at eval time the artifact is simply the agent's live workspace and the
prompt is the shipped ``instruction.md``. Everything downstream of "task dict"
is reused verbatim, so the two paths score identically.

CONTRACT WITH test.sh
─────────────────────
* Always exits 0. Harbor resolves the trial from ONE of two files
  (``harbor/verifier/verifier.py::_resolve_rewards``): ``reward.txt`` is a score,
  ``infra_failure.json`` is the retryable ``VerifierInfraFailureError``. This
  driver writes the sentinel FIRST and swaps it for a reward only once it has
  something to say about the artifact — see "NO MEASUREMENT, NO SCORE" below.
* Writes ``reward.txt`` (single float), ``metrics.json``, ``verdict.json``,
  and the full evidence tree (plan/trajectory/frames/report.md) under ``--out``.
* An empty workspace (the Harbor *non-op* calibration run) has no HTML entry,
  which is a determinate fact about the attempt, not an error: reward 0.0.

NO MEASUREMENT, NO SCORE
────────────────────────
0.0 is a claim about the ARTIFACT, so it may only be written when the artifact
was actually looked at. This driver used to floor ``reward.txt`` to 0.0 before
doing any work, which meant every way of dying — OOM, SIGKILL, ``timeout``, a
browser that would not launch — reached the reward distribution as "worthless
artifact". On eval25_o5 that cost a real trial: the agent's own
``npm install`` (``playwright: ^1.62.1`` in its package.json) pruned the
Chromium revision this verifier needed out of the shared registry, and the
trial was recorded ``reward=0.0000 items=0/0`` — nothing measured, a definite
number. Invisible in exactly the way a missing trial is not.

So the pre-write is a *sentinel*, not a zero, and it is cleared only when a
determinate outcome exists (scored / no_artifact / geopolicy_blocked). Failures
that produced no evidence at all keep it and write no reward, which Harbor
surfaces as ``VerifierInfraFailureError``: retried when ``max_retries > 0``,
resumable with ``harbor jobs resume -f VerifierInfraFailureError``, and never
averaged into a score. A partial measurement (an error AFTER some items were
graded) still scores, because there the evidence exists.
Set ``IV_INFRA_SENTINEL=0`` to restore the old floor-to-zero behaviour.

DEPENDENCIES
────────────
Hard: ``playwright`` (+ its Chromium), python3 stdlib. The judges reach their
gateways through ``service.llm_client``, which is stdlib-only (urllib).
Optional: ``Pillow`` (image downscale for the judge payload, blank-frame
channel), ``numpy`` (pixel-delta evidence), ``psutil`` (browser watchdog). All
three degrade to "abstain" rather than failing, so the verifier still runs on a
bare Playwright image — but the implanter installs them by default because the
evidence they add is what several checklist modes are scored on.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
# The implanted two-tree payload: <tests>/iv/{scripts,qa_data_analysis_system}.
# `scripts.interactive_verify._bootstrap` derives its REPO_ROOT from its own
# location (parents[2]), so mirroring the repo layout under `iv/` makes
# `ensure_service_path()` resolve `service.*` with no patching at all.
IV_ROOT = HERE / "iv"
if str(IV_ROOT) not in sys.path:
    sys.path.insert(0, str(IV_ROOT))


def _fnum(x: Any, default: float) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _load_config(path: str | None) -> dict[str, Any]:
    """Config file, then environment overrides.

    The file is baked at implant time so an instance is self-contained and can
    be verified with no runner cooperation. The env layer exists so an operator
    can re-point endpoints or rotate a key WITHOUT rebuilding 138 images — the
    credentials in the baked file are the fallback, not the only channel.
    """
    cfg: dict[str, Any] = {"gen": {}, "judge": {}, "run": {}, "reward": {},
                           "geopolicy": {}}
    if path and Path(path).is_file():
        try:
            loaded = json.loads(Path(path).read_text(encoding="utf-8"))
            for k in cfg:
                if isinstance(loaded.get(k), dict):
                    cfg[k].update(loaded[k])
        except (OSError, ValueError) as exc:
            print(f"[iv] WARNING: unreadable config {path}: {exc}", flush=True)

    env = os.environ.get
    # Checklist-generation identity comes ONLY from iv_config.json's "gen"
    # block, optionally overridden by IV_GEN_* env. There is NO JUDGE_* fallback
    # and no built-in default: a missing value must fail loudly instead of
    # silently retargeting the checklist gen model.
    for key, var in (("base_url", "IV_GEN_BASE_URL"),
                     ("api_key", "IV_GEN_API_KEY"),
                     ("model", "IV_GEN_MODEL")):
        val = env(var)
        if val:
            cfg["gen"][key] = val
    # Unified judge credentials: only JUDGE_API_BASE (with /v1), JUDGE_API_KEY and
    # JUDGE_MODEL need to be provided; the legacy IV_* vars still win if set.
    for key, var, fallback in (("base_url", "IV_JUDGE_BASE_URL", "JUDGE_API_BASE"),
                               ("api_key", "IV_JUDGE_API_KEY", "JUDGE_API_KEY"),
                               ("vlm_model", "IV_VLM_MODEL", "JUDGE_MODEL"),
                               ("text_model", "IV_TEXT_MODEL", "JUDGE_MODEL"),
                               ("aesthetics_model", "IV_AESTHETICS_MODEL", "JUDGE_MODEL")):
        val = env(var) or env(fallback)
        if val:
            cfg["judge"][key] = val
    if env("IV_AESTHETICS_WEIGHT"):
        cfg["reward"]["aesthetics_weight"] = _fnum(env("IV_AESTHETICS_WEIGHT"), 0.0)
    if env("IV_COVERAGE_MODE"):
        cfg["reward"]["coverage_mode"] = env("IV_COVERAGE_MODE")
    if env("IV_MIN_DETERMINATE_FRAC"):
        cfg["reward"]["min_determinate_frac"] = _fnum(env("IV_MIN_DETERMINATE_FRAC"), 0.0)
    # Benchmark-mode scoring knobs. Off by default (empty env or unset) → the
    # calibrated distillation behaviour. `IV_GATE_POLICY=weighted` switches the
    # critical gate to advisory partial credit (see `_functional_score`);
    # `IV_CRITICAL_WEIGHT` multiplies the weight of default-weight critical items.
    if env("IV_GATE_POLICY"):
        cfg["reward"]["gate_policy"] = env("IV_GATE_POLICY")
    if env("IV_CRITICAL_WEIGHT"):
        cfg["reward"]["critical_weight"] = _fnum(env("IV_CRITICAL_WEIGHT"), 1.0)
    # Guardrail knobs. On by default and baked per instance; these exist so an
    # operator can retune an ALREADY-BUILT fleet without re-implanting 138 images.
    # An empty string is honoured as an empty list, so a record-only pass — every
    # finding still attached to every verdict, nothing zeroed — is both of
    #   IV_GEOPOLICY_GATE_ACTIONS="" IV_GEOPOLICY_GATE_RULE_KEYS=""
    # (clearing only one leaves the other tier gating).
    if env("IV_GEOPOLICY_ENABLED"):
        cfg["geopolicy"]["enabled"] = (
            env("IV_GEOPOLICY_ENABLED").strip().lower()
            not in ("0", "false", "no", "off"))
    if env("IV_GEOPOLICY_GATE_ACTIONS") is not None:
        cfg["geopolicy"]["gate_actions"] = [
            a.strip().upper()
            for a in env("IV_GEOPOLICY_GATE_ACTIONS").split(",") if a.strip()]
    if env("IV_GEOPOLICY_GATE_RULE_KEYS") is not None:
        cfg["geopolicy"]["gate_rule_keys"] = [
            k.strip() for k in env("IV_GEOPOLICY_GATE_RULE_KEYS").split(",") if k.strip()]
    return cfg


def _read_prompt(explicit: str | None, workspace: str) -> str:
    """The build request the checklist must be faithful to.

    Implant time copies ``instruction.md`` next to this driver, because the
    instance root is not reliably present inside the container (only ``tests/``
    and the workspace are). The other lookups are belt-and-braces for hand-run
    invocations.
    """
    for cand in (explicit, str(HERE / "iv_task_prompt.txt"),
                 str(HERE.parent / "instruction.md"),
                 str(Path(workspace) / "instruction.md")):
        if cand and Path(cand).is_file():
            try:
                txt = Path(cand).read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if txt:
                return txt
    return ""


def _shipped_checklist(instance_id: str) -> list[dict[str, Any]] | None:
    """The benchmark's own checklist, normalized — the generation FALLBACK.

    Generation is the requested behaviour (checks grounded in what the agent
    actually rendered), but a gen-endpoint outage would otherwise turn every
    task in a sweep into a 0.0 that reads as a broken agent. Where the export
    already ships a real, human/benchmark-authored checklist, scoring against it
    is a legitimate verdict rather than a fabricated one — and the source is
    recorded on the verdict so the two are never pooled silently.
    """
    src = HERE / "iv_shipped_checklist.json"
    if not src.is_file():
        return None
    try:
        raw = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, list) or not raw:
        return None
    from service.verify import checklist as _checklist
    try:
        return _checklist.validate_checklist(
            [dict(it, task_id=instance_id) for it in raw])
    except ValueError as exc:
        print(f"[iv] shipped checklist rejected: {exc}", flush=True)
        return None


def _build_task(workspace: str, instance_id: str, prompt: str, gen_call,
                allowed_modes: list[str] | None, timings: dict[str, float]) -> dict[str, Any]:
    """Assemble the same task dict ``pipeline.verify_task`` gets from every other
    corpus loader, but sourced from a live workspace instead of an artifact tar."""
    from scripts.interactive_verify import harbor_loader, loader
    from scripts.interactive_verify.static_server import to_http_url
    from service.verify.htmleval_exec import _pick_entry

    ws = Path(workspace).resolve()
    entry = _pick_entry(ws) if ws.is_dir() else None
    if entry is None:
        raise NoArtifact(f"{instance_id}: no HTML entry under {ws}")

    t0 = time.monotonic()
    gen_records: list[dict[str, Any]] = []
    source = "generated"
    try:
        items = harbor_loader.build_checklist(instance_id, prompt, entry, gen_call,
                                              allowed_modes=allowed_modes,
                                              gen_records=gen_records)
    except Exception as exc:  # noqa: BLE001 — gen outage must not become a fake zero
        fallback = _shipped_checklist(instance_id)
        if not fallback:
            raise
        print(f"[iv] checklist generation failed ({type(exc).__name__}: "
              f"{str(exc)[:160]}); falling back to the shipped checklist",
              flush=True)
        items, source = fallback, "shipped"
    timings["load_checklist_s"] = round(time.monotonic() - t0, 2)

    return {
        "task_id": instance_id,
        "prompt": prompt,
        "items": items,
        "checklist_source_impl": source,
        "gen_meta": harbor_loader.summarize_gen(gen_records),
        "workspace": str(ws),
        # Local HTTP, never file:// — ES-module/importmap artifacts are
        # CORS-blocked under an opaque file:// origin and never boot, which
        # would zero-score most 3D/game builds for a reason of our own making.
        "entry_url": to_http_url(entry),
        "entry_path": str(entry),
        "code": loader.gather_code(entry),
        "needs_mobile": harbor_loader.needs_mobile(prompt),
    }


# Signatures that name WHY a run produced no evidence. Triage only — the decision
# to treat a run as non-evaluable is made by _is_unmeasured() on the evidence, not
# by matching strings, so an unrecognised crash is still classified correctly (as
# `verifier_crash`) rather than silently scored.
_INFRA_SIGNATURES = (
    ("browser_missing", ("executable doesn't exist", "browsertype.launch",
                         "please run the following command to download")),
    ("browser_crashed", ("target page, context or browser has been closed",
                         "browser has been closed", "connection closed")),
    ("payload_broken", ("no module named", "cannot import name")),
    ("out_of_memory", ("cannot allocate memory", "memoryerror", "oom")),
    ("disk_full", ("no space left on device",)),
)


def _infra_kind(detail: str) -> str:
    low = (detail or "").lower()
    for kind, sigs in _INFRA_SIGNATURES:
        if any(sig in low for sig in sigs):
            return kind
    return "verifier_crash"


def _is_unmeasured(status: str, verdict: dict[str, Any]) -> bool:
    """True when the run produced no evidence about the artifact whatsoever.

    Deliberately narrow: only the error paths, and only when NOTHING was graded.
    An error after some items were decided is a partial measurement and keeps its
    score — converting those to infra failures would retry (and eventually
    discard) trials whose evidence we actually hold. The reverse mistake is the
    expensive one, so the split is on evidence, not on exception type.

    The residual risk runs the other way: an artifact so broken that it hangs or
    kills the browser before a single item is decided now ends the trial
    unresolved instead of scoring 0.0. That is the failure mode we want — Harbor
    retries it, and a deterministic one surfaces as a visible unresolved trial
    rather than as an invisible zero in the mean. `_infra_kind` names it
    `verifier_crash`, which is exactly the bucket to go read.
    """
    if status != "error":
        return False
    return not (verdict.get("items") or int(verdict.get("total_items") or 0))


def _write_infra_sentinel(path: str, kind: str, detail: str, instance_id: str = "") -> None:
    """Harbor's own channel for "this trial is not evaluable" (verifier.py:33)."""
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(
            {"kind": kind, "detail": (detail or "")[:1000],
             "instance_id": instance_id, "source": "iv_verify.py"},
            ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"[iv] WARNING: could not write infra sentinel {path}: {exc}", flush=True)


def _unlink_quiet(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _truthy_env(var: str) -> bool:
    """Interpret a stringified bool from the env-export tuple.

    `str(True)` bakes to "True", `str(False)` to "False"; the existing IV_*
    knobs are typically numeric so this parses both shapes. Empty/unset = False.
    """
    v = (os.environ.get(var) or "").strip().lower()
    return v not in ("", "0", "false", "no", "off")


def _agent_starved(agent_log_path: str = "/logs/agent/claude-code.txt"
                   ) -> tuple[bool, str]:
    """Did the agent barely run, or did the artifact just not build?

    A gateway TTFT fat-tail (>~120s to first token) starves an agent turn
    budget: it exits 0 with num_turns=1 and no artifact, indistinguishable
    downstream from a legitimate broken attempt. In distillation that is fine
    (the run is a wash and averages out); in an academic benchmark it is a
    measurement of the gateway, not the model, and must be classified as
    infra so the reconciler excludes + retries it.

    Signature: the last JSON line of the Claude Code stream is `{"type": "result",
    "num_turns": ..., "duration_api_ms": ..., "is_error": ..., ...}`
    (scripts/realtime_arena/lib/claudecode.mjs:195-207). "Starved" is a
    conjunction — low turns AND either is_error or the whole run was one
    long-latency API call. Any Read/Write/Edit tool call bumps num_turns, so
    num_turns > 1 already means the agent got somewhere; a broken app it
    actually worked on stays a determinate 0.0.
    """
    try:
        raw = Path(agent_log_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False, "no agent log"
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            nt = int(obj.get("num_turns") or 0)
            api_ms = int(obj.get("duration_api_ms") or 0)
            is_err = bool(obj.get("is_error"))
            # >1 turn = the agent produced tokens the harness ingested. That's
            # not TTFT starvation regardless of the outcome.
            if nt > 1:
                return False, f"num_turns={nt}"
            # 0 or 1 turn is only starvation if there's an infra fingerprint:
            # is_error, or the sole turn was mostly waiting on the API. A
            # normal 1-turn "I refuse" answer with fast API time is NOT infra.
            if is_err or api_ms >= 60_000:
                return True, (f"num_turns={nt} is_error={is_err} "
                              f"duration_api_ms={api_ms}")
            return False, f"num_turns={nt} api_ms={api_ms}"
        break  # a non-result last line — nothing to classify
    return False, "no result line"


class GeopolicyBlocked(RuntimeError):
    """The artifact violates the territorial / values policy — a determinate 0.0.

    Raised BEFORE the browser launches and before any gateway call: the scan is
    offline and deterministic, so a violating artifact costs no judge tokens. Like
    NoArtifact this is a fact about the attempt, not an error.
    """


class NoArtifact(RuntimeError):
    """The attempt produced nothing renderable — a determinate 0.0, not an error."""


def _functional_score(verdict: dict[str, Any], mode: str, min_frac: float,
                      gate_policy: str = "hard", critical_weight_mult: float = 1.0
                      ) -> tuple[float, str | None]:
    """The functionality component of the reward, and why it was gated.

    ``verdict["score"]`` is the verifier's native number: weighted pass-ratio
    over the DETERMINATE items, with both hard gates already applied
    (unrenderable -> 0.0; a determinate failure on a critical item -> 0.0).
    That is the number the verifier was calibrated against human arena labels
    with, so it is the default here — changing the formula would decalibrate it.

    But it is a ratio over what could be MEASURED, and as a reward that has a
    sharp edge the sweep context does not have. Measured on a probe artifact:
    3 of 12 items determinate (the executor's selectors missed a
    placeholder-only input), all 3 satisfied, native score 1.000. In a sweep the
    ``determinate`` column is right there to filter on; a Harbor reward is one
    scalar and nothing downstream can tell 3-of-12 from 12-of-12.

    Three knobs, all off by default so the shipped behaviour IS the calibrated
    behaviour:

    ``reward.coverage_mode``
        ``"determinate"`` (default) — native: satisfied / determinate.
        ``"total"`` — conservative: satisfied / all items, so an item the
        executor never reached earns nothing instead of being excluded from the
        denominator. Use when the reward feeds training directly and an
        over-credited zero-evidence pass is the expensive mistake.
    ``reward.min_determinate_frac``
        Coverage floor. Below it the run did not measure enough to be a
        measurement: reward 0.0 with an explicit gate reason, rather than a
        confident number resting on two checks.
    ``reward.gate_policy``
        ``"hard"`` (default) — the native critical gate stands: a determinate
        failure on any critical item zeros the whole reward (calibrated).
        ``"weighted"`` (benchmark) — the critical gate becomes ADVISORY. A
        critical item is scored as an ordinary determinate item, but at
        ``critical_weight_mult`` × its authored weight (only when that weight is
        the default 1.0 — an explicit author weight is respected). A failed
        critical costs its weight but no longer collapses an otherwise-working
        artifact to 0.0. This de-bimodalizes an academic benchmark; it must NOT
        relax the renderability / no-artifact / geopolicy / infra gates, which
        are not "one behaviour missed" but "nothing to score".

    ``determinate_frac`` is recorded on the metrics either way, so a sweep can
    always re-derive any policy after the fact.
    """
    native = _fnum(verdict.get("score"), 0.0)
    total = int(verdict.get("total_items") or 0)
    det = int(verdict.get("determinate") or 0)
    frac = (det / total) if total else 0.0

    score, gate = native, None
    # A gated verdict is already 0.0 and its reason (unrenderable / critical
    # failure) is the specific fact; do not overwrite it with a coverage story.
    gated_zero = native <= 0.0 and verdict.get("gate_reason")
    # The critical gate is the ONLY gate `gate_policy="weighted"` overrides. Every
    # other gate reason (renderability, no-artifact, "no determinate evidence",
    # geopolicy, infra) means the artifact could not be MEASURED, not that it
    # missed one behaviour — those keep the hard 0.0 even in weighted mode.
    is_critical_gate = str(verdict.get("gate_reason") or "").startswith("critical:")

    if gate_policy == "weighted" and (not gated_zero or is_critical_gate):
        # Recompute weighted partial credit over the DETERMINATE items, ignoring
        # the critical gate. Criticals are heavier determinate items, not a cliff.
        def _eff_w(it: dict[str, Any]) -> float:
            w = float(it.get("weight", 1.0))
            if it.get("critical") and abs(w - 1.0) < 1e-9:
                w *= critical_weight_mult
            return w
        det_items = [it for it in (verdict.get("items") or [])
                     if (it.get("result") or {}).get("satisfied") is not None]
        den = sum(_eff_w(it) for it in det_items)
        num = sum(_eff_w(it) for it in det_items
                  if (it.get("result") or {}).get("satisfied") is True)
        score = (num / den) if den > 0 else 0.0
        gate = None  # the reward is no longer gated; native gate_reason kept on verdict
    elif mode == "total" and not gated_zero:
        if total:
            sat_w = sum(float(it.get("weight", 1.0)) for it in verdict.get("items") or []
                        if (it.get("result") or {}).get("satisfied") is True)
            all_w = sum(float(it.get("weight", 1.0)) for it in verdict.get("items") or [])
            score = (sat_w / all_w) if all_w > 0 else 0.0
        else:
            score = 0.0
    # Coverage floor applies after any reshaping, but never re-zeros a run that a
    # non-critical gate already zeroed (its reason is the more specific fact).
    if min_frac > 0 and not (gated_zero and not is_critical_gate) and frac < min_frac:
        score, gate = 0.0, f"coverage {det}/{total} < {min_frac:.2f}"
    return max(0.0, min(1.0, score)), gate


def _reward_from(verdict: dict[str, Any], weight: float, mode: str,
                 min_frac: float, gate_policy: str = "hard",
                 critical_weight_mult: float = 1.0) -> tuple[float, str | None]:
    """Blend functionality with aesthetics into Harbor's single scalar.

    Aesthetics is scored independently and RECORDED ONLY by default (weight 0):
    it grades how the page looks, which is orthogonal to whether it works, and
    folding it into the same scalar would let a beautiful non-functional page
    outscore a plain working one. Set ``reward.aesthetics_weight`` (or
    ``IV_AESTHETICS_WEIGHT``) to blend it in when that is what a run wants.
    """
    score, gate = _functional_score(verdict, mode, min_frac, gate_policy,
                                    critical_weight_mult)
    if weight <= 0:
        return score, gate
    aes = verdict.get("aesthetics") or {}
    total = aes.get("total_100")
    if total is None:  # unscored aesthetics must not silently pull the reward down
        return score, gate
    blended = (1.0 - weight) * score + weight * (_fnum(total, 0.0) / 100.0)
    return max(0.0, min(1.0, blended)), gate


def _cpu_delta(before: dict[str, Any]) -> dict[str, Any]:
    """Throttling incurred over the run, or just the snapshot if we lost the
    baseline. Never raises — this is diagnostics, not scoring."""
    try:
        import iv_cpu_report
        after = iv_cpu_report.snapshot()
        return iv_cpu_report.delta(before, after) if before else after
    except BaseException:  # noqa: BLE001
        return before or {}



# ---------------------------------------------------------------------------
# stdout reporting
#
# `verifier/test-stdout.txt` is the only artefact a reviewer reads without
# opening JSON, so it has to answer two questions on its own: *why is the score
# what it is*, and *did this artifact trip the territorial/values guard*. Before
# this existed the whole answer was one METRICS line carrying `geopolicy=REVIEW`
# with no rule, no file and no per-item result — enough to know something was
# flagged, useless for deciding what to do about it.
#
# Both helpers are best-effort by construction: a formatting bug here must never
# change a reward, so every one of them is wrapped and swallows its own errors.
# ---------------------------------------------------------------------------
_RULE = "[iv] " + "-" * 68


def _p(line: str = "") -> None:
    print(f"[iv] {line}" if line else "[iv]", flush=True)


def _clip(text: object, n: int = 200) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[: n - 1] + "\u2026"


def _print_geopolicy_report(geo: dict, cfg: dict) -> None:
    """Print the guard's decision AND its evidence.

    Printed on every path, including `clean`: "the scan ran and found nothing" is
    a different statement from "no scan output appeared", and only one of them is
    checkable after the fact.
    """
    try:
        pol = (cfg or {}).get("geopolicy") or {}
        print(_RULE, flush=True)
        if geo.get("absent"):
            # Distinct from "disabled": the modules are not in this export at
            # all, so no env var can turn the guard back on. Say which, or a
            # reader retunes IV_GEOPOLICY_* and wonders why nothing changes.
            _p("geopolicy guard: NOT IMPLANTED in this export (no guard modules; "
               "IV_GEOPOLICY_* cannot enable it — re-implant to add it)")
            print(_RULE, flush=True)
            return
        if not geo.get("enabled", True):
            _p("geopolicy guard: DISABLED for this instance (nothing gated)")
            print(_RULE, flush=True)
            return
        _p("geopolicy guard: verdict=%s gated=%s files_scanned=%s wall=%ss%s"
           % (geo.get("verdict") or "-", "YES" if geo.get("gated") else "no",
              geo.get("files_scanned"), geo.get("wall_s"),
              "  TRUNCATED(budget expired: clean is UNPROVEN)"
              if geo.get("truncated") else ""))
        _p("  policy: gate_actions=%s gate_rule_keys=%s budget=%ss"
           % (",".join(pol.get("gate_actions") or ["BLOCK"]),
              ",".join(pol.get("gate_rule_keys") or ["<default>"]),
              pol.get("budget_s", 90)))

        if geo.get("error"):
            # Fail-open is deliberate, but it must be loud: a silent fail-open is
            # an unenforced guard that looks like a compliant artifact.
            _p("  SCAN FAILED — artifact scored on its merits (fail open, NOT a zero)")
            _p("  error: %s" % _clip(geo["error"], 240))
            print(_RULE, flush=True)
            return

        findings = geo.get("findings") or {}
        if not findings:
            _p("  content rules matched: none")
        else:
            _p("  content rules matched: %d" % len(findings))
            for key, f in sorted(findings.items()):
                gating = (f.get("action") in (pol.get("gate_actions") or ["BLOCK"])
                          or key in (pol.get("gate_rule_keys") or []))
                _p("    %-24s category=%s action=%s scope=%s count=%s %s"
                   % (key, f.get("category"), f.get("action"), f.get("scope"),
                      f.get("count"), "<== GATES" if gating else "(recorded only)"))
                for rel in (f.get("files") or [])[:5]:
                    _p("        %s" % _clip(rel, 100))
                extra = int(f.get("files_total") or 0) - len(f.get("files") or [])
                if extra > 0:
                    _p("        ... +%d more file(s)" % extra)

        sources = geo.get("sources") or {}
        if sources:
            # 1,118 of 1,124 REVIEWs in the 19,291-instance sweep were this class,
            # so say plainly that it does not gate — otherwise every reviewer has
            # to rediscover that a wikipedia link in a comment is not a violation.
            _p("  remote sources flagged: %d (provenance only — does NOT gate)"
               % len(sources))
            for url, meta in list(sources.items())[:5]:
                _p("    [%s] %s" % (meta.get("action"), _clip(url, 96)))
            if len(sources) > 5:
                _p("    ... +%d more (full list in checklist_verdict.json"
                   " -> geopolicy.sources)" % (len(sources) - 5))

        if geo.get("gated"):
            _p("  *** GATED: %s -- reward forced to 0.0 regardless of rendering"
               " or checklist ***" % geo.get("gate"))
        print(_RULE, flush=True)
    except BaseException as exc:  # noqa: BLE001 — reporting must never gate
        print(f"[iv] WARNING: geopolicy report failed to render: "
              f"{type(exc).__name__}: {exc}", flush=True)


def _print_checklist_report(verdict: dict, reward: float,
                            reward_gate: object) -> None:
    """Per-item PASS / FAIL / INDET with the judge's reason for each.

    This is the "why is the score what it is" half. Indeterminate items are called
    out separately because they are *excluded* from the denominator, not counted as
    failures — the single most misread number in this file's output.
    """
    try:
        items = verdict.get("items") or []
        print(_RULE, flush=True)
        _p("checklist: source=%s renderable=%s items=%s determinate=%s"
           " satisfied=%s score=%s"
           % (verdict.get("checklist_source"), verdict.get("renderable"),
              verdict.get("total_items"), verdict.get("determinate"),
              verdict.get("satisfied"), verdict.get("score")))
        if reward_gate or verdict.get("gate_reason"):
            _p("  reward gate: %s  (reward=%.4f)"
               % (reward_gate or verdict.get("gate_reason"), reward))
        # One defect per line, kind first, detail barely clipped. When every item
        # comes back INDET the defect IS the explanation for the score, so clipping
        # it to fit a list repr on one line hides the only sentence that matters --
        # and hides the second and third defects entirely.
        for tag, cap in (("build_defects", 8), ("plan_defects", 6)):
            ds = verdict.get(tag) or []
            if not ds:
                continue
            _p("  %s (%d):" % (tag, len(ds)))
            for d in ds[:cap]:
                if isinstance(d, dict):
                    _p("    %s" % (d.get("kind") or "?"))
                    if d.get("detail"):
                        _p("        %s" % _clip(d["detail"], 600))
                else:
                    _p("    %s" % _clip(d, 400))
            if len(ds) > cap:
                _p("    ... +%d more (full list in checklist_verdict.json -> %s)"
                   % (len(ds) - cap, tag))
        if verdict.get("exec_truncated"):
            _p("  exec_truncated=True (execution budget expired mid-plan)")
        if not items:
            _p("  no items graded")
            print(_RULE, flush=True)
            return
        _p("   #  result  crit  wt    item")
        for i, it in enumerate(items, 1):
            res = (it.get("result") or {})
            sat = res.get("satisfied")
            label = {True: "PASS ", False: "FAIL "}.get(sat, "INDET")
            _p("  %2d  %s   %s   %-4s  %s"
               % (i, label, "*" if it.get("critical") else " ",
                  it.get("weight"), _clip(it.get("title"), 88)))
            why = res.get("rationale")
            if why:
                _p("        why: %s" % _clip(why, 220))
        print(_RULE, flush=True)
    except BaseException as exc:  # noqa: BLE001
        print(f"[iv] WARNING: checklist report failed to render: "
              f"{type(exc).__name__}: {exc}", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="implanted interactive verifier")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--instance-id", required=True)
    ap.add_argument("--config", default=str(HERE / "iv_config.json"))
    ap.add_argument("--prompt-file", default=None)
    ap.add_argument("--out", default="/logs/verifier/interactive")
    ap.add_argument("--reward-file", default="/logs/verifier/reward.txt")
    ap.add_argument("--infra-failure-file",
                    default="/logs/verifier/infra_failure.json",
                    help="Harbor's non-evaluable-trial sentinel. Written before any "
                         "work and cleared only when a determinate outcome exists.")
    ap.add_argument("--metrics-file", default="/logs/verifier/metrics.json")
    ap.add_argument("--verdict-file", default="/logs/verifier/checklist_verdict.json")
    args = ap.parse_args(argv)

    t_start = time.monotonic()
    cfg = _load_config(args.config)
    print("[iv] ==================== LLM ENDPOINT DIAGNOSTIC ====================", flush=True)
    _e = os.environ.get
    print(f"[iv] sandbox env : JUDGE_API_BASE={_e('JUDGE_API_BASE') or '<unset>'} "
          f"JUDGE_MODEL={_e('JUDGE_MODEL') or '<unset>'} "
          f"JUDGE_API_KEY={'set' if _e('JUDGE_API_KEY') else '<unset>'}", flush=True)
    print(f"[iv] sandbox env : IV_GEN_BASE_URL={_e('IV_GEN_BASE_URL') or '<unset>'} "
          f"IV_GEN_MODEL={_e('IV_GEN_MODEL') or '<unset>'} "
          f"IV_GEN_API_KEY={'set' if _e('IV_GEN_API_KEY') else '<unset>'}", flush=True)
    _g = cfg.get('gen') or {}
    print(f"[iv] resolved gen: base_url={_g.get('base_url') or '<NONE>'} "
          f"model={_g.get('model') or '<NONE>'} "
          f"api_key={'set' if _g.get('api_key') else '<unset>'}", flush=True)
    _j = cfg.get('judge') or {}
    print(f"[iv] resolved judge: vlm_model={_j.get('vlm_model') or '<NONE>'} "
          f"text_model={_j.get('text_model') or '<NONE>'} "
          f"aesthetics_model={_j.get('aesthetics_model') or '<NONE>'}", flush=True)
    try:
        import socket as _socket
        from urllib.parse import urlparse as _urlparse
        _u = _urlparse(_g.get('base_url') or '')
        if _u.hostname:
            _port = _u.port or (443 if _u.scheme == 'https' else 80)
            _infos = _socket.getaddrinfo(_u.hostname, _port, proto=_socket.IPPROTO_TCP)
            _ips = sorted({i[4][0] for i in _infos})
            _s = _socket.socket(); _s.settimeout(3.0)
            try:
                _s.connect((_infos[0][4][0], _port)); _conn = 'TCP connect OK'
            except Exception as _ce:
                _conn = f'TCP connect FAILED: {_ce}'
            finally:
                _s.close()
            print(f"[iv] DNS/网络     : {_u.hostname} -> {_ips} (port {_port}); {_conn}", flush=True)
        else:
            print("[iv] DNS/网络     : gen.base_url 为空,跳过探测", flush=True)
    except Exception as _de:
        print(f"[iv] DNS/网络     : 探测异常 {_de!r}", flush=True)
    print("[iv] ===============================================================", flush=True)
    runc, rewc = cfg["run"], cfg["reward"]
    weight = _fnum(rewc.get("aesthetics_weight"), 0.0)
    cov_mode = str(rewc.get("coverage_mode") or "determinate")
    min_frac = _fnum(rewc.get("min_determinate_frac"), 0.0)
    gate_policy = str(rewc.get("gate_policy") or "hard")
    critical_wm = _fnum(rewc.get("critical_weight"), 1.0)

    # Executor/browser budgets, exported before anything imports the modules that
    # read them at import time. The RSS ceiling is deliberately set BELOW the
    # container's memory limit: a leaking artifact must be killed by our own
    # watchdog (which records the kill and lets the task finish with the frames
    # it has) rather than by the kernel OOM killer, which takes the whole
    # verifier process with it and leaves no reward at all.
    for key, var in (("exec_budget_s", "IV_EXEC_BUDGET_S"),
                     ("browser_hard_limit_s", "IV_BROWSER_HARD_LIMIT_S"),
                     ("browser_rss_limit_mb", "IV_BROWSER_RSS_LIMIT_MB"),
                     ("judge_workers", "IV_JUDGE_WORKERS"),
                     # Benchmark-mode toggles. Baked to False in distillation, so
                     # the env is set to "False" and downstream readers see falsy.
                     ("bench_coverage", "IV_BENCH_COVERAGE"),
                     ("bench_infra_detect", "IV_BENCH_INFRA_DETECT"),
                     ("wedge_fail_fast", "IV_BENCH_WEDGE_BLOCKLIST")):
        if runc.get(key) is not None and not os.environ.get(var):
            os.environ[var] = str(runc[key])

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in (args.reward_file, args.metrics_file, args.verdict_file):
        Path(f).parent.mkdir(parents=True, exist_ok=True)
    # Claim "not evaluable" BEFORE doing any work, and earn the right to a number
    # later. See "NO MEASUREMENT, NO SCORE" in the module docstring: an
    # interpreter-level death (OOM, SIGKILL, `timeout`) leaves this sentinel
    # behind, which Harbor reads as a retryable infrastructure failure instead of
    # as a 0.0 verdict on an artifact nobody looked at.
    infra_file = str(args.infra_failure_file)
    use_sentinel = os.environ.get("IV_INFRA_SENTINEL", "1").strip().lower() \
        not in ("0", "false", "no", "off")
    if use_sentinel:
        _unlink_quiet(args.reward_file)   # a stale file would shadow the sentinel
        _write_infra_sentinel(
            infra_file, "verifier_died_early",
            "iv_verify.py started but never reached a verdict (killed by OOM / "
            "SIGKILL / the test.sh timeout, or crashed before scoring)",
            args.instance_id)
    else:
        Path(args.reward_file).write_text("0.0\n", encoding="utf-8")

    # How much CPU this container was actually given, and whether the browser
    # will size its thread pools for it. Sampled BEFORE any work so the throttle
    # counters can be differenced across the run. See iv_cpu_report for why this
    # only reports: the mismatch is fixable at the runner (--cpuset-cpus), and
    # an in-container os.sched_setaffinity measured 3x WORSE.
    cpu_before: dict[str, Any] = {}
    try:
        sys.path.insert(0, str(HERE))
        import iv_cpu_report
        cpu_before = iv_cpu_report.snapshot()
        print(f"[iv] {iv_cpu_report.describe(cpu_before)}", flush=True)
    except BaseException as exc:  # noqa: BLE001 - diagnostics never break a run
        print(f"[iv] cpu report unavailable: {type(exc).__name__}: {exc}", flush=True)

    verdict: dict[str, Any] = {}
    reward_gate: str | None = None
    reward = 0.0
    status = "error"
    err: str | None = None
    timings: dict[str, float] = {}
    webgl_ok: bool | None = None
    # Initialised here so the metrics/verdict write path is valid even if the try
    # block dies before the scan runs (e.g. an import error in the payload).
    geo: dict[str, Any] = {"enabled": None, "verdict": None, "gated": False,
                           "gate": None, "keys": [], "error": "not run"}

    try:
        from scripts.interactive_verify import harbor_loader, models, pipeline, run

        # Territorial / values guardrail. FIRST, before the browser and before any
        # gateway call: the scan is offline and deterministic, so a violating
        # artifact costs nothing to reject. `evaluate` never raises — a scanner
        # failure returns gated=False with an error recorded, so a bug here cannot
        # manufacture zeros (which is how the Playwright prune corrupted a whole
        # arm of the 138-task run).
        # OPTIONAL SUBSYSTEM. An export implanted with `--no-geopolicy`, or one
        # the implanter later stripped with `--revert-guard`, carries no guard
        # modules at all, so this import is allowed to fail. Two things make the
        # handling here non-obvious and both matter:
        #   * the ImportError must be caught HERE. The blanket handler below
        #     turns anything it sees into status="error" reward=0.0, so a
        #     guard-free export would score a fleet of zeros for the one reason
        #     that is not about the artifact.
        #   * the absence is RECORDED, as verdict="not_implanted", not swallowed
        #     into the "clean" shape. "No guard was shipped" and "the guard ran
        #     and found nothing" are different claims, and only one of them can
        #     be checked after the fact from a metrics row.
        try:
            from scripts.interactive_verify import geopolicy as _geopolicy
        except ImportError:
            geo = {"enabled": False, "verdict": "not_implanted", "gated": False,
                   "gate": None, "keys": [], "findings": {}, "sources": {},
                   "files_scanned": 0, "truncated": False, "error": None,
                   "wall_s": 0.0, "absent": True}
        else:
            geo = _geopolicy.evaluate(args.workspace, cfg.get("geopolicy"))
        _print_geopolicy_report(geo, cfg)
        if geo.get("gated"):
            raise GeopolicyBlocked(geo["gate"])

        prompt = _read_prompt(args.prompt_file, args.workspace)
        if not prompt:
            print("[iv] WARNING: empty build prompt — checklist will be "
                  "artifact-grounded only", flush=True)

        # Capability pre-flight, ADVISORY here (unlike the sweep CLI, which
        # aborts). A browser that silently lost WebGL serves every page with
        # getContext('webgl2') === null, so 3D artifacts render their own "no
        # WebGL" sheet and score 0.0 with an accurate and entirely wrong
        # rationale. Inside a single graded container we cannot abort — Harbor
        # would read the missing reward as a broken agent — so we record the
        # capability on the metrics instead, which makes a systematically-wrong
        # sweep countable after the fact.
        try:
            from scripts.interactive_verify._smoke_webgl import main as _webgl
            webgl_ok = (_webgl() == 0)
        except BaseException as exc:  # noqa: BLE001
            print(f"[iv] webgl pre-flight raised: {type(exc).__name__}: {exc}", flush=True)
            webgl_ok = False
        if not webgl_ok:
            print("[iv] WARNING: WebGL2 pre-flight FAILED — 3D artifacts may be "
                  "scored against their fallback sheet", flush=True)

        gen_call = harbor_loader.build_gen_call(cfg["gen"])
        vlm_call, text_call, aes_call, planner_call = models.build_calls(cfg["judge"])

        task = _build_task(args.workspace, args.instance_id, prompt, gen_call,
                           runc.get("allowed_modes"), timings)
        pipeline.write_json_atomic(out_dir / "checklist_gen.json", {
            "checklist_source": task["checklist_source_impl"],
            "items": task["items"], "gen_meta": task["gen_meta"]})

        page, finalize = run._build_page_factory()()
        try:
            verdict = pipeline.verify_task(
                task, page, out_dir=out_dir,
                vlm_call=vlm_call, text_call=text_call,
                aesthetics_call=aes_call, planner_call=planner_call,
                max_frames=int(runc.get("max_frames") or 12),
                max_steps=int(runc.get("max_steps") or 40),
                step_timeout_s=int(runc.get("step_timeout_s") or 15))
        finally:
            try:
                finalize(out_dir)
            except Exception:  # noqa: BLE001 — teardown must never eat the verdict
                pass

        verdict["checklist_source"] = task["checklist_source_impl"]
        err = str(verdict.get("error") or "").strip() or None
        status = "error" if err else "scored"
        if not err:
            reward, reward_gate = _reward_from(
                verdict, weight, cov_mode, min_frac, gate_policy, critical_wm)
        timings.update(verdict.get("timings") or {})

    except GeopolicyBlocked as exc:
        # A policy violation is a determinate zero, like no-artifact: the artifact
        # was found and read, and it is disqualified. Distinct `status` so a sweep
        # can count these separately from broken builds.
        status, err, reward = "geopolicy_blocked", None, 0.0
        reward_gate = str(exc)
        print(f"[iv] GEOPOLICY BLOCK — reward 0.0 ({exc})", flush=True)
        verdict = {"score": 0.0, "renderable": None, "method": "interactive",
                   "total_items": 0, "determinate": 0, "indeterminate": 0,
                   "satisfied": 0, "items": [], "gate_reason": str(exc),
                   "aesthetics": None, "error": None}

    except NoArtifact as exc:
        # Determinate, not an error: this is exactly what the non-op calibration
        # run must produce, and what a genuinely failed build produces too.
        status, err, reward = "no_artifact", str(exc), 0.0
        print(f"[iv] {exc}", flush=True)
        verdict = {"score": 0.0, "renderable": False, "method": "interactive",
                   "total_items": 0, "determinate": 0, "indeterminate": 0,
                   "satisfied": 0, "items": [], "gate_reason": "no artifact",
                   "aesthetics": None, "error": None}
    except BaseException as exc:  # noqa: BLE001 — any failure is a recorded zero
        status, err, reward = "error", f"{type(exc).__name__}: {exc}"[:400], 0.0
        traceback.print_exc()
        verdict = {"score": 0.0, "renderable": None, "method": "interactive",
                   "total_items": 0, "determinate": 0, "indeterminate": 0,
                   "satisfied": 0, "items": [], "aesthetics": None, "error": err}

    # Attached on EVERY path — clean, non-gating REVIEW/WARN, blocked, or scanner
    # error. Non-gating findings are a corpus-level provenance signal worth keeping
    # even though they must not zero a reward.
    verdict["geopolicy"] = geo

    # Did this run learn ANYTHING about the artifact? If not, the 0.0 above is a
    # statement about our own infrastructure, and Harbor has a channel for that.
    # Computed here, before metrics, so metrics/verdict/stdout all agree.
    infra_kind = None
    if use_sentinel and _is_unmeasured(status, verdict):
        infra_kind = _infra_kind(err or "")
        status = "infra_failure"
        verdict["gate_reason"] = f"infra:{infra_kind}"
    # Benchmark-mode TTFT/starvation reclassification: a `no_artifact` run
    # where the agent barely ran (num_turns<=1 with an infra fingerprint) is a
    # measurement of the gateway, not the model — exclude+retry via the
    # reconciler rather than count as a determinate zero. Distillation leaves
    # `no_artifact` alone (the run averages out over k trials).
    elif (use_sentinel and status == "no_artifact"
          and _truthy_env("IV_BENCH_INFRA_DETECT")):
        starved, detail = _agent_starved()
        if starved:
            infra_kind = "agent_ttft_starved"
            status = "infra_failure"
            verdict["gate_reason"] = f"infra:{infra_kind}"
            reward = 0.0
            print(f"[iv] TTFT-STARVED — reclassified as infra ({detail})",
                  flush=True)

    aes = verdict.get("aesthetics") or {}
    _tot = int(verdict.get("total_items") or 0)
    _det = int(verdict.get("determinate") or 0)
    metrics = {
        "instance_id": args.instance_id,
        "reward": round(reward, 4),
        "status": status,
        "method": "interactive",
        "checklist_source": verdict.get("checklist_source"),
        "functional_score": verdict.get("score"),
        "renderable": verdict.get("renderable"),
        "total_items": verdict.get("total_items"),
        "determinate": verdict.get("determinate"),
        "indeterminate": verdict.get("indeterminate"),
        "satisfied": verdict.get("satisfied"),
        # How much of the checklist was actually decided. A reward of 1.0 over
        # 3 determinate items of 12 is not the same measurement as 1.0 over 12,
        # and the single scalar Harbor reads cannot carry that — so it is
        # recorded here, where a sweep can filter or re-derive on it.
        "determinate_frac": round(_det / _tot, 4) if _tot else 0.0,
        "coverage_mode": cov_mode,
        "min_determinate_frac": min_frac,
        "gate_policy": gate_policy,
        "critical_weight": critical_wm,
        "reward_gate": reward_gate,
        "gate_reason": verdict.get("gate_reason"),
        "build_defects": verdict.get("build_defects") or [],
        # None on every evaluable trial. Non-null means `reward` below is NOT a
        # judgement of the artifact and must be excluded from any mean.
        "infra_failure": infra_kind,
        "exec_truncated": verdict.get("exec_truncated"),
        "aesthetics_total_100": aes.get("total_100"),
        "aesthetics_scores": aes.get("scores"),
        "aesthetics_weight": weight,
        "webgl_ok": webgl_ok,
        "geopolicy_verdict": geo.get("verdict"),
        "geopolicy_gated": geo.get("gated"),
        "geopolicy_keys": geo.get("keys"),
        "geopolicy_truncated": geo.get("truncated"),
        "geopolicy_error": geo.get("error"),
        # Sizing evidence. `oversized_pools`/`throttled_frac` explain a slow or
        # variable run without having to re-run it, and separate "the artifact
        # is bad" from "the container was starved".
        "cpu": _cpu_delta(cpu_before),
        "error": err,
        "wall_s": round(time.monotonic() - t_start, 2),
        "timings": timings,
    }

    if infra_kind:
        # reward.txt WINS over infra_failure.json in Harbor's resolver
        # (verifier.py::_resolve_rewards), so the sentinel only takes effect if no
        # reward file is left behind. The early floor already removed it; this is
        # belt-and-braces against a future path that writes one.
        _unlink_quiet(args.reward_file)
        _write_infra_sentinel(infra_file, infra_kind, err or "unknown", args.instance_id)
        print(f"[iv] INFRA FAILURE ({infra_kind}) — NO reward written, this trial is "
              f"not evaluable and must not be scored 0.0\n[iv]   reason: {err}",
              flush=True)
    else:
        Path(args.reward_file).write_text(f"{reward:.4f}\n", encoding="utf-8")
        # Earned a determinate outcome — retract the "not evaluable" claim.
        _unlink_quiet(infra_file)
    for path, obj in ((args.metrics_file, metrics), (args.verdict_file, verdict)):
        try:
            Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        except OSError as exc:
            print(f"[iv] WARNING: could not write {path}: {exc}", flush=True)

    _print_checklist_report(verdict, reward, reward_gate)

    print(f"METRICS reward={reward:.4f} status={status} "
          f"score={metrics['functional_score']} "
          f"items={metrics['determinate']}/{metrics['total_items']} "
          f"gate={reward_gate or verdict.get('gate_reason') or '-'} "
          f"aesthetics={metrics['aesthetics_total_100']} "
          f"source={metrics['checklist_source']} "
          f"geopolicy={geo.get('verdict') or '-'}"
          f"{'(GATED)' if geo.get('gated') else ''} "
          f"wall={metrics['wall_s']}s", flush=True)
    print("INTERACTIVE results starts here")
    if infra_kind:
        print("INFRA_FAILURE")          # neither PASSED nor FAILED: not measured
    else:
        print("PASSED" if reward > 0 else "FAILED")
    print("INTERACTIVE results ends here")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
