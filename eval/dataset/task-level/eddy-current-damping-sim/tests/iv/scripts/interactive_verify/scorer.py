#!/usr/bin/env python3
"""Stage 3 — score each checklist item from the recorded trajectory.

Per item we send the FULL trajectory (a text step-log + the source code, plus a
downsampled set of frames for VLM-mode items) and ask for a binary satisfied
verdict. VLM-mode items route to the vision model; text-only modes route to the
text model. Results aggregate via the shared scoring module so the verdict is
schema-comparable to the checklist method.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

# Per-item judging is I/O-bound (one LLM call each) and independent, so we fan the
# items out across a small thread pool. Kept modest because a batch of tasks
# already runs several scorers concurrently — this MULTIPLIES against --workers,
# and a shared VLM gateway (the sidecar's gemini-3.5-flash) saturates well below
# workers×_JUDGE_WORKERS heavy image calls, timing out at ~18-way concurrency.
# Override via IV_JUDGE_WORKERS to keep total in-flight VLM calls within budget.
import os
import re
_JUDGE_WORKERS = max(1, int(os.environ.get("IV_JUDGE_WORKERS", "6")))

from scripts.interactive_verify._bootstrap import ensure_service_path
from scripts.interactive_verify._images import image_block
# Same normalizer the planner stamps trajectory steps with, so a model-labeled
# `for_item: "item 3"` and a back-filled `"3"` resolve to one pair_map key.
from scripts.interactive_verify.planner import normalize_item_id
from scripts.interactive_verify import _pixdiff

ensure_service_path()
from service.verify import scoring  # noqa: E402

_SYS = (
    "You are a strict frontend-artifact verifier. Given ONE checklist item and "
    "evidence from a real browser session — an execution trace of user "
    "interactions, the page's accessibility tree (the structure and state a user "
    "perceives), the accessibility-tree CHANGE each action produced, and "
    "screenshots — decide whether the check was SATISFIED. Judge what the "
    "artifact DID, as a user would see it; you are not reviewing source code. "
    "Cite what you actually observed. Use the full range — do NOT default to "
    "satisfied when evidence is missing. If the evidence does not settle the "
    "question, answer null rather than guessing.\n\n"
    # The single largest disagreement with human labels. Calibrated against the
    # arena study (96 arm-tasks, human per-item labels): of 49 items the judge
    # failed that the human passed, 33 were coverage failures of OUR harness, not
    # defects of the artifact — and 22 of those were items with no planned step of
    # their own, failed on timeout text belonging to OTHER items in the shared
    # trace. false is a claim about the artifact; only what we drove supports it.
    #
    # The first draft of this paragraph stopped after "answer null" and swung the
    # matrix the other way: false_fail 71 -> 6, but coverage 71% -> 21%, because
    # the judge began abstaining on items the AX tree and screenshots settled on
    # their own. Hence the second half — abstain only when the check genuinely
    # NEEDED an interaction we failed to perform.
    "CRITICAL distinction. `false` means the artifact was DRIVEN to the relevant "
    "state and did the wrong thing. It does NOT mean our automation could not get "
    "there. The trace is SHARED by every checklist item, so a 'Timeout' or "
    "'selector not found' line in it usually belongs to a DIFFERENT item's action. "
    "Never answer false because the session did not reach a state, a step timed "
    "out, an element was not clickable, or preconditions were never met — that is "
    "our harness failing, and the correct answer is null.\n\n"
    "Equally, do NOT retreat to null when the evidence you DO have settles the "
    "check. Many checks are about what is present, laid out, labeled or styled, "
    "and the accessibility tree plus the screenshots answer those completely with "
    "no interaction at all — decide them true or false on that evidence. Reserve "
    "null for a check that genuinely required an interaction we failed to "
    "perform. Judge the artifact on the evidence you have, not on the evidence "
    "you wish you had.\n\n"
    "Respond with ONLY: "
    '{"satisfied": true|false|null, "rationale": "one or two sentences citing evidence"}.'
)


def select_frames(frame_paths: list[str], max_frames: int = 12) -> list[str]:
    """Dedup consecutive-identical basenames, then uniform-sample to max_frames
    (always keeping the first and last)."""
    if not frame_paths:
        return []
    deduped = [frame_paths[0]]
    for f in frame_paths[1:]:
        if f != deduped[-1]:
            deduped.append(f)
    if len(deduped) <= max_frames:
        return deduped
    if max_frames <= 2:
        return [deduped[0], deduped[-1]][:max_frames]
    # uniform stride over the middle, force-including first and last
    n = len(deduped)
    idxs = sorted({0, n - 1} | {round(i * (n - 1) / (max_frames - 1))
                                for i in range(max_frames)})
    return [deduped[i] for i in idxs][:max_frames]


def _is_directional(item: dict[str, Any]) -> bool:
    return any(isinstance(s, dict) and s.get("capture") == "before_after"
               for s in (item.get("action_plan") or []))


# Chars of artifact MARKUP sent to the judge, as a fallback for the little the
# accessibility tree cannot answer (chiefly: what a <canvas> is supposed to be).
# It is deliberately small. The judge's evidence is what the artifact DID in a
# real browser — the trace, the AX tree, the per-action AX diff, the frames —
# because that is what the checklist items assert. Source code only ever
# supported an inference about what should happen, and a truncated slice of it
# actively produced false verdicts: on b0811 the judge failed working features
# with "the source code is truncated before the ... logic can be verified".
CODE_BUDGET = 15000
CODE_TAIL_SHARE = 0.55

# Script bodies are excluded outright. Whether the behavior works is settled by
# EXECUTING the action plan and reading the resulting state change, not by
# reading the handler that was supposed to implement it — and script bodies are
# what pushed the median artifact (~200KB total) an order of magnitude past any
# budget, evicting the markup that is actually useful here.
_DROP_SUFFIXES = (".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx")
_SECTION = re.compile(r"\n===== (?P<rel>[^\n]+) =====\n")


def _is_vendor(rel: str) -> bool:
    rel = rel.replace("\\", "/")
    return ("node_modules/" in rel or rel.startswith(".git/")
            or "__pycache__" in rel or "/vendor/" in rel or rel.startswith("vendor/"))


def _structure_only(code: str) -> str:
    """Markup and styles from a gathered multi-file blob; scripts and vendor out.

    `loader.gather_code` concatenates files behind `===== <relpath> =====`
    headers. The entry document has no header (it is emitted first), so text
    before the first header is always kept.
    """
    code = code or ""
    marks = list(_SECTION.finditer(code))
    if not marks:
        return code
    out = [code[:marks[0].start()]]
    for i, m in enumerate(marks):
        rel = m.group("rel")
        end = marks[i + 1].start() if i + 1 < len(marks) else len(code)
        low = rel.lower()
        if low.endswith(_DROP_SUFFIXES) or _is_vendor(rel):
            continue
        out.append(code[m.start():end])
    return "".join(out)


def _code_excerpt(code: str) -> str:
    """The markup block for the judge prompt, labeled by what it actually is.

    The label used to read "SOURCE CODE (truncated):" unconditionally. On an
    artifact well under the budget the judge was therefore told its evidence was
    incomplete when it was complete, and it used that as grounds to decline:
    a 9.5KB artifact had four items failed with "the source code is truncated
    before the ... logic can be verified". A false claim about the evidence
    produces a false verdict, so the label now states the truth in both cases.
    """
    code = _structure_only(code)
    head_note = ("MARKUP AND STYLES (scripts omitted on purpose — behavior is "
                 "established by the execution trace and accessibility-tree "
                 "changes above, not by reading handlers)")
    if len(code) <= CODE_BUDGET:
        return f"{head_note}, {len(code)} chars:\n{code}"
    tail = int(CODE_BUDGET * CODE_TAIL_SHARE)
    head = CODE_BUDGET - tail
    elided = len(code) - CODE_BUDGET
    return (f"{head_note}; excerpt of {len(code)} chars: first {head} and last "
            f"{tail}, {elided} chars elided from the MIDDLE. Do NOT infer that a "
            f"feature is absent from its absence here — decide from the trace, "
            f"the accessibility tree, and the screenshots:\n"
            f"{code[:head]}\n\n... [{elided} chars elided] ...\n\n{code[-tail:]}")


# The executor's own notes about how a step was delivered, always written in
# square brackets and appended to whatever the planner authored. See the use
# site in `_trace_text` for why only these are shown to the judge.
_HARNESS_NOTE_RE = re.compile(r"\[([^\[\]]+)\]")


def _trace_text(trajectory: dict[str, Any]) -> str:
    lines = []
    for s in trajectory.get("steps", []):
        tail = f"ERROR: {s['error']}" if s.get("error") else (s.get("result") or "ok")
        # Which item this step was planned FOR. The trace is built once and shared
        # by every item, so without this tag an "ERROR: Timeout" line reads as
        # generic evidence of brokenness — and the judge attributed it to whatever
        # item it was currently deciding. Measured on the arena calibration: 22 of
        # 49 human-disagreeing failures were items with no planned step at all,
        # failed on another item's timeout. The tag lets the per-item prompt say
        # "lines for other items are not evidence about your check".
        owner = s.get("for_item")
        tag = f"[for item {normalize_item_id(owner)}] " if owner is not None else ""
        lines.append(f"step {s['step_no']}: {tag}{s['action']} "
                     f"{s.get('target', '')} -> {tail}")
        # The accessibility diff comes FIRST and gets the bigger budget: it is
        # the direct evidence that the action changed state, where the innerText
        # prefix below is a 200-char sample that routinely shows nothing even
        # when a modal opened or a control flipped.
        axd = (s.get("ax_diff") or "")
        if axd:
            for ln in axd.splitlines()[:24]:
                lines.append(f"  ax {ln}")
        # Scroll steps carry measured before/after positions (see
        # executor.execute_scroll). Without this the trace said only
        # "scroll -> ok", and a judge reading "the heading never changed" could
        # not tell whether the app ignored a real scroll or the page never moved
        # — two b0811 artifacts were gated to 0.000 on that ambiguity.
        sc = s.get("scroll")
        if isinstance(sc, dict):
            lines.append(
                f"  scroll: moved={sc.get('moved')} via {sc.get('method')} "
                f"[scrollY,scrollX,sum(elem.scrollTop)] "
                f"{sc.get('before')} -> {sc.get('after')}")
        dom = (s.get("dom_snapshot") or "")[:200]
        if dom:
            lines.append(f"  dom: {dom}")
        # Harness notes the executor recorded about the step's DELIVERY, e.g.
        # `[not force-clicked: target is occluded]` or `[drag not dispatched:
        # press point covered by #gate]`. Without them a correctly-diagnosed
        # occlusion reaches the judge as a bare timeout — indistinguishable from
        # a control that does not exist, which is exactly the confusion that made
        # dLmjAh5's buried transport row read as "DEMO produced no audio".
        # Only the bracketed harness notes are surfaced: a step's planner-authored
        # `note` states the expected outcome, and showing that to the judge would
        # be handing it the answer.
        for frag in _HARNESS_NOTE_RE.findall(s.get("note") or ""):
            lines.append(f"  harness: {frag}")
    errs = trajectory.get("page_errors") or []
    if errs:
        lines.append("page_errors: " + "; ".join(str(e) for e in errs[:5]))
    return "\n".join(lines)


def _ax_block(trajectory: dict[str, Any]) -> str:
    """The page's accessibility tree, before and after the session.

    Sent whole rather than as a diff: an item like "the header shows a score"
    is answered by the tree's CONTENT, while "clicking pause halts it" is
    answered by the per-step diffs already in the trace.
    """
    init = trajectory.get("ax_initial") or ""
    final = trajectory.get("ax_final") or ""
    if not init and not final:
        return ""
    out = [f"ACCESSIBILITY TREE AT LOAD (what a user first sees):\n{init}"]
    if final and final != init:
        out.append(f"ACCESSIBILITY TREE AFTER THE LAST ACTION:\n{final}")
    return "\n\n".join(out)


def _judge_item(item: dict[str, Any], trace: str, code: str, frame_files: list[str],
                frames_dir: Path, vlm_call: Callable, text_call: Callable,
                pair_map: dict[str, list[tuple[str, str]]] | None = None,
                failed_map: dict[str, int] | None = None,
                ax_block: str = "",
                axdiff_map: dict[str, list[str]] | None = None,
                ran_map: dict[str, int] | None = None,
                unreached: set[str] | None = None,
                pixdelta_map: dict[str, list[dict[str, float]]] | None = None,
                burst_map: dict[str, list[list[dict[str, float]]]] | None = None,
                burstframe_map: dict[str, list[list[str]]] | None = None) -> dict[str, Any]:
    # Each checklist item declares a judging MODE. VLM-mode items assert something
    # visual ("the modal fades in") and need the screenshots, so they route to the
    # vision model; everything else is decidable from the trace + AX tree and
    # routes to the cheaper text model. An item wanting VLM but with no frames
    # degrades to text rather than sending an image-less "VLM" call.
    is_vlm = str(item.get("mode", "")).startswith("VLM")
    _title = item.get("title")
    _key = normalize_item_id(item.get("check_item_id"))
    # This item's OWN state change, stated separately from the shared trace. A
    # directional check asserts that one interaction changed something; the
    # +/- lines for exactly its steps are that claim's direct evidence, and
    # burying them in a 40-step trace is how the judge missed them.
    _own = (axdiff_map or {}).get(_key) or []
    own_block = ""
    if _own:
        joined = "\n".join(_own)
        own_block = ("ACCESSIBILITY-TREE CHANGE PRODUCED BY THIS CHECK'S OWN "
                     f"ACTION(S):\n{joined}\n\n")
    # Measured pixel change for this check's own before/after frames. The AX tree
    # is the right instrument for a DOM widget and the WRONG one for a <canvas>:
    # a WebGL scene that rotates never touches the tree, so the line above always
    # reads "(no accessibility-tree change)" and the judge took that as a
    # confident negative (b0811 `Eer4ptb` item 3, where the sphere demonstrably
    # moved). We do not suppress the AX line — for a DOM check "nothing changed"
    # IS the answer, and these artifacts often animate continuously, so pixel
    # motion alone proves nothing. We ADD the measurement beside it and say
    # explicitly which instrument answers which question. An unmeasurable delta
    # (frame missing, Pillow absent) contributes nothing rather than "no change"
    # (DEGRADE-NEVER-GATE).
    _deltas = (pixdelta_map or {}).get(_key) or []
    _lines = [ln for ln in (_pixdiff.describe(d) for d in _deltas) if ln]
    if _lines:
        own_block += (
            "\n".join(_lines)
            + ("\n\nUse the right instrument for this check: the accessibility "
               "tree answers questions about DOM state (a value readout, a "
               "pressed state, a class toggle) and CANNOT see inside a <canvas>. "
               "If this check concerns something rendered on a canvas, "
               "'(no accessibility-tree change)' is expected and is NOT evidence "
               "of failure — judge it on the measured pixel change and the "
               "screenshots instead.\n\n"))
    # DEFECT 67: the motion timeline across the observation window. A pair of
    # endpoint frames answers "did it differ"; a check that says "flowing",
    # "orbits", "animates", or "Pause stops it" is asking whether it was still
    # moving, which only consecutive samples can answer. The user named the Pause
    # case exactly: "the screenshots before and after pausing are indeed
    # different", so BEFORE != AFTER is compatible with a pause that WORKED.
    _tls = (burst_map or {}).get(_key) or []
    _tl_lines = [ln for ln in (_pixdiff.describe_timeline(t) for t in _tls) if ln]
    if _tl_lines:
        own_block += "\n".join(_tl_lines) + "\n\n"
    # The trace is shared across items and its step lines are tagged with the item
    # each was planned for. Say so explicitly, and say which tag is THIS item's,
    # so an unrelated item's timeout is not read as this check failing.
    _trace_note = (
        f"EXECUTION TRACE (shared by all checklist items; steps are tagged "
        f"[for item N] with the item they were planned for — THIS check is item "
        f"{_key}. A failure on a step tagged for a different item says nothing "
        f"about this check):\n"
        if _key is not None else "EXECUTION TRACE:\n")
    text = (
        (f"CHECK TITLE: {_title}\n" if _title else "")
        + f"CHECK: {item.get('checking_content')}\n"
        f"EXPECTED: {item.get('expected_result')}\n\n"
        f"{_trace_note}{trace}\n\n"
        + own_block
        + (f"{ax_block}\n\n" if ax_block else "")
        + _code_excerpt(code)
    )
    # Directional check: judge it on ITS OWN before/after pair (threaded through by
    # the planner/executor via for_item), labeled BEFORE/AFTER, instead of the
    # shared downsampled frame set. Missing pair -> fall through to shared frames
    # (never fabricate evidence). `pair_map` holds only COMPLETE (pre, post)
    # tuples, so a step that captured a `pre_frame` but never ran (no
    # `post_frame`) contributes nothing: the item degrades to indeterminate
    # rather than being judged against a lone frame labeled BEFORE with no AFTER
    # — a determinate verdict from half a pair can force a critical gate.
    pairs = (pair_map or {}).get(_key) if _is_directional(item) else None
    # No step planned for this item ever ran, and at least one was attempted and
    # errored: the interaction never happened, so nothing about the requirement
    # was observed. Falling through to the shared trace here is what made this a
    # false negative — the trace contains "ERROR: Timeout ..." for the very steps
    # this item owns, and the judge reads that as determinate evidence of failure
    # ("the button could not be clicked, so no update occurred"). A determinate
    # False on a critical item forces the whole task to 0.0, so an
    # executor/selector defect became an artifact verdict. On b0810 this was 13 of
    # 42 critical gates. Absent evidence is indeterminate, which by contract never
    # gates (DEGRADE-NEVER-GATE).
    #
    # The condition is "no successful step", NOT "not directional and has no
    # pair": the guard used to be scoped to directional items, and b0811's
    # `9nZimj5` walked straight past it — a critical item whose plan declared no
    # before_after capture, whose only step timed out on the selector, gated the
    # task to 0.0 on a harness failure. Whether an item asked for a frame pair has
    # no bearing on whether its interaction was observed.
    if not pairs and not (ran_map or {}).get(_key):
        n_failed = (failed_map or {}).get(_key, 0)
        if n_failed:
            return {"satisfied": None,
                    "rationale": (f"indeterminate: all {n_failed} action(s) planned for this "
                                  "check failed to execute (selector/actionability error), so "
                                  "the interaction was never performed and no before/after "
                                  "evidence exists")}
        # Same absence of evidence by a different route: steps WERE planned for
        # this check but the executor never reached them (it stopped at
        # `conclude`, max_steps, or the wall-clock budget). b0811 `zgFoa46`:
        # the plan carried a probe for each of 12 items, the loop stopped at the
        # first, and item 7 — never attempted — was judged against the shared
        # trace and gated the artifact to 0.0. `n_failed == 0` here, so the
        # errored-step branch above does not catch it. An unattempted
        # interaction is indeterminate, which never gates.
        if _key in (unreached or set()):
            return {"satisfied": None,
                    "rationale": ("indeterminate: the action(s) planned for this check were "
                                  "never reached — execution stopped earlier in the plan "
                                  "(step budget, time budget, or an earlier conclude) — so "
                                  "the interaction was never performed")}
    if pairs:
        # DEFECT 67: when the probe sampled its window, show the sequence rather
        # than the two endpoints. Two frames show a displacement; three or more
        # show a trajectory, which is what "is it still moving" needs. Labels are
        # explicit and ordered so the model cannot mistake the direction of time.
        _bframes = (burstframe_map or {}).get(_key) or []
        _seq = bool(_bframes) and len(_bframes) == len(pairs)
        note = text + (
            "\n\nThe screenshots are frames captured IN TIME ORDER across this "
            "check's interaction, labeled BEFORE (immediately before the action), "
            "DURING (samples spread across the observation window after it), and "
            "AFTER (the end of that window). Judge the CHANGE across the "
            "sequence. Consecutive DURING frames that are indistinguishable mean "
            "the artifact had come to rest; ones that keep changing mean it was "
            "still in motion."
            if _seq else
            "\n\nThe screenshots are labeled BEFORE and AFTER the "
            "interaction; judge the CHANGE between them.")
        blocks: list[dict[str, Any]] = [{"type": "text", "text": note}]
        for i, (pre, post) in enumerate(pairs):
            mid = [(f"DURING {j + 1}:", n)
                   for j, n in enumerate(_bframes[i])] if _seq else []
            # Label from the tuple position, not the flat index — an odd entry
            # anywhere used to flip BEFORE/AFTER for every frame after it.
            for label, name in [("BEFORE:", pre)] + mid + [("AFTER:", post)]:
                blk = image_block(str(frames_dir / name))
                if blk is not None:
                    blocks.append({"type": "text", "text": label})
                    blocks.append(blk)
        content: Any = blocks
        call = vlm_call
    elif is_vlm and frame_files:
        blocks = [{"type": "text", "text": text}]
        for name in frame_files:
            blk = image_block(str(frames_dir / name))
            if blk is not None:
                blocks.append(blk)
        content = blocks
        call = vlm_call
    else:
        content = text
        call = text_call
    messages = [{"role": "system", "content": _SYS}, {"role": "user", "content": content}]
    try:
        obj = call(messages)
        sat = obj.get("satisfied")
        satisfied = bool(sat) if isinstance(sat, bool) else None
        rationale = str(obj.get("rationale", ""))[:800]
    except Exception as exc:  # noqa: BLE001
        return {"satisfied": None, "rationale": f"judge error: {exc}"[:200]}
    return {"satisfied": satisfied, "rationale": rationale}


def _shared_inputs(trajectory: dict[str, Any], max_frames: int,
                   frames_dir: Path | None = None) -> dict[str, Any]:
    """Everything `_judge_item` needs that is the same for every item.

    Factored out of `score` so the offline prompt reconstruction (see
    `reconstruct_prompts`, used by the inspector UI) assembles its inputs
    through this exact function rather than a copy. A copy would agree on the
    day it was written and quietly diverge afterwards, and a reconstruction that
    silently differs from what the judge saw is worse than none — it invites
    conclusions about a prompt that was never sent.
    """
    # Downsample frames + flatten the trace ONCE and share them across items —
    # every item judges against the same trajectory, only the CHECK differs.
    frame_files = select_frames(trajectory.get("frames") or [], max_frames)
    trace = _trace_text(trajectory)
    ax_block = _ax_block(trajectory)

    # Map each item's before/after evidence from the trajectory steps, keyed by
    # the for_item stamp the planner/executor threaded through (normalized, so a
    # model-labeled "item 3" joins the back-filled "3"). A directional check is
    # judged on ITS OWN pair, not the shared frame set. Only COMPLETE (pre, post)
    # tuples are recorded: a half pair is not before/after evidence and must not
    # produce a determinate verdict (DEGRADE-NEVER-GATE).
    pair_map: dict[str, list[tuple[str, str]]] = {}
    # Count, per item, the steps that were attempted for it and ERRORED. An item
    # with no pair but a non-zero count had its interaction attempted and fail at
    # the browser level (selector never matched, element never became clickable),
    # which is an absence of evidence — not evidence of absence. _judge_item uses
    # this to return indeterminate instead of letting the judge read the error
    # text out of the shared trace as a determinate failure.
    failed_map: dict[str, int] = {}
    # The AX +/- lines produced by each item's OWN steps, so a directional check
    # is shown its own state change instead of having to find it in the shared
    # trace. Keyed the same way as pair_map. A "(no accessibility-tree change)"
    # line is kept deliberately: for a check asserting something should have
    # changed, that IS the evidence, and it must be distinguishable from a step
    # whose tree was never captured (which contributes nothing).
    axdiff_map: dict[str, list[str]] = {}
    # Steps attempted for an item that actually RAN (no error). This is what
    # separates "we drove the artifact and this is what it did" from "we never
    # reached the artifact at all". An item with zero successful steps and at
    # least one failed one has no observation behind it, whether or not its plan
    # asked for a before/after frame pair — see the guard in _judge_item.
    ran_map: dict[str, int] = {}
    # Items the executor planned steps for but never reached (see
    # `unreached_items` in executor.run_trajectory). Normalized to the same key
    # space as the maps above so a model-labeled "item 4" joins the bare "4".
    unreached: set[str] = {normalize_item_id(x)
                           for x in trajectory.get("unreached_items") or []}
    # Measured pixel change per item, one entry per COMPLETE frame pair, in the
    # same order as pair_map. Only computed when the caller supplies the frames
    # directory; an unreadable frame or a missing Pillow yields no entry at all,
    # never a zero — "could not measure" must not read as "nothing moved".
    pixdelta_map: dict[str, list[dict[str, float]]] = {}
    # DEFECT 67. Frame-to-frame deltas across each probe's burst, one list per
    # capture. This is the channel a before/after PAIR cannot provide: whether
    # the artifact was still moving at the end of the window. See
    # `_pixdiff.burst_timeline` for why a pair cannot answer a Pause check.
    burst_map: dict[str, list[list[dict[str, float]]]] = {}
    # The burst frame names themselves, so the VLM can be shown a mid-window
    # frame beside BEFORE/AFTER — a three-frame strip shows a trajectory where
    # two endpoints show only a displacement.
    burstframe_map: dict[str, list[list[str]]] = {}
    _fdir = Path(frames_dir) if frames_dir else None
    for s in trajectory.get("steps", []):
        fid = s.get("for_item")
        if fid is None:
            continue
        key = normalize_item_id(fid)
        pre, post = s.get("pre_frame"), s.get("post_frame")
        if pre and post:
            pair_map.setdefault(key, []).append((pre, post))
            if _fdir is not None:
                d = _pixdiff.frame_delta(str(_fdir / pre), str(_fdir / post))
                if d:
                    pixdelta_map.setdefault(key, []).append(d)
        # DEFECT 67: the motion timeline. Requires the pair too — a burst with no
        # post_frame belongs to a step that errored, and its frames time a no-op.
        _burst = s.get("burst_frames") or []
        if pre and post and _burst and _fdir is not None:
            # Deliberately EXCLUDES the pre frame. The pre->first-sample interval
            # spans the action itself, so it carries the action's own discrete
            # effect — a focus ring, a class toggle, a panel opening — which is
            # not motion. Measured in the smoke: a page that never animates read
            # 0.25% on that interval purely from the focus ring its own click
            # drew, which classified a static artifact as "was moving, then came
            # to rest". Whether the action changed anything is what the
            # BEFORE/AFTER pair (pixdelta_map) already answers; this instrument
            # answers whether the artifact was still in motion afterwards, and it
            # must not be contaminated by the first question.
            seq = [str(_fdir / n) for n in (list(_burst) + [post])]
            tl = _pixdiff.burst_timeline(seq)
            if tl:
                burst_map.setdefault(key, []).append(tl)
                burstframe_map.setdefault(key, []).append(list(_burst))
        if s.get("error"):
            failed_map[key] = failed_map.get(key, 0) + 1
        else:
            ran_map[key] = ran_map.get(key, 0) + 1
            if s.get("ax_diff"):
                # Only for steps that actually RAN: the diff around an errored
                # no-op says "nothing changed" about an interaction that never
                # happened, which is precisely the false-negative this pipeline
                # exists to avoid (DEGRADE-NEVER-GATE).
                axdiff_map.setdefault(key, []).append(
                    f"[step {s.get('step_no')}: {s.get('action')} "
                    f"{s.get('target', '')}]\n{s['ax_diff']}")
    return {"trace": trace, "frame_files": frame_files, "ax_block": ax_block,
            "pair_map": pair_map, "failed_map": failed_map,
            "axdiff_map": axdiff_map, "ran_map": ran_map, "unreached": unreached,
            "pixdelta_map": pixdelta_map, "burst_map": burst_map,
            "burstframe_map": burstframe_map}


def score(items: list[dict[str, Any]], trajectory: dict[str, Any], code: str,
          vlm_call: Callable, text_call: Callable, *, frames_dir: Path,
          max_frames: int = 12) -> dict[str, Any]:
    sh = _shared_inputs(trajectory, max_frames, frames_dir)
    frame_files = sh["frame_files"]
    fdir = Path(frames_dir)
    judged: list[dict[str, Any]] = [dict(it) for it in items]

    def _run(it: dict[str, Any]) -> dict[str, Any]:
        return _judge_item(it, sh["trace"], code, frame_files, fdir,
                           vlm_call, text_call,
                           sh["pair_map"], sh["failed_map"], sh["ax_block"],
                           sh["axdiff_map"], sh["ran_map"], sh["unreached"],
                           sh["pixdelta_map"], sh["burst_map"],
                           sh["burstframe_map"])

    # Items are independent and each is one blocking LLM call, so fan them across a
    # thread pool. pool.map keeps result order aligned to `judged`. Single item ->
    # skip the pool entirely (no thread overhead, cleaner to debug/step).
    if len(judged) <= 1:
        for it in judged:
            it["result"] = _run(it)
    else:
        workers = min(_JUDGE_WORKERS, len(judged))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for it, result in zip(judged, pool.map(_run, judged)):
                it["result"] = result
    verdict = scoring.aggregate(judged, renderable=bool(trajectory.get("renderable")))
    verdict["method"] = "interactive"
    verdict["checklist_source"] = "embedded"
    verdict["items"] = judged
    verdict["frames"] = frame_files
    return verdict


def reconstruct_prompts(items: list[dict[str, Any]], trajectory: dict[str, Any],
                        code: str, *, frames_dir: Path,
                        max_frames: int = 12) -> list[dict[str, Any]]:
    """Rebuild the judge prompt for each item WITHOUT calling a model.

    The pipeline persists each item's parsed verdict but not the prompt that
    produced it — storing every prompt would add megabytes per task across a
    17.8k-instance sweep for evidence that is fully derivable. This replays the
    real judging path against the stored trajectory with capture stubs in place
    of the two model callables, so the returned messages are assembled by
    `_judge_item` itself.

    It is a faithful RECONSTRUCTION, not a recording. It agrees with what was
    sent as long as the stored trajectory, `_SYS`, and the assembly code are
    unchanged since the run; edit the judge prompt and the reconstruction shows
    today's prompt against yesterday's verdict. Callers displaying this to a
    human must say so.

    Image blocks are replaced by `{"type": "image_ref", "frame": <name>}`: the
    base64 payload is the frame file the UI can already serve, and inlining it
    would make a single task's response tens of megabytes.

    Returns one record per item, in `items` order:
        {check_item_id, title, route, messages, no_call_reason}
    `route` is "vlm" | "text" | None, and is None exactly when the item
    short-circuited to indeterminate before any model call — in which case
    `no_call_reason` carries the rationale the scorer recorded and `messages` is
    empty. That case is not a gap in the reconstruction; it is the answer.
    """
    sh = _shared_inputs(trajectory, max_frames, frames_dir)
    fdir = Path(frames_dir)
    out: list[dict[str, Any]] = []

    for it in items:
        cap: dict[str, Any] = {}

        def _mk(route: str) -> Callable:
            def _call(messages: list[dict[str, Any]]) -> dict[str, Any]:
                cap["route"] = route
                cap["messages"] = messages
                # _judge_item only reads .get() off this, and the result is
                # discarded — the prompt is what we came for.
                return {"satisfied": None, "rationale": ""}
            return _call

        res = _judge_item(it, sh["trace"], code, sh["frame_files"], fdir,
                          _mk("vlm"), _mk("text"),
                          sh["pair_map"], sh["failed_map"], sh["ax_block"],
                          sh["axdiff_map"], sh["ran_map"], sh["unreached"],
                          sh["pixdelta_map"], sh["burst_map"],
                          sh["burstframe_map"])
        out.append({
            "check_item_id": it.get("check_item_id"),
            "title": it.get("title"),
            "route": cap.get("route"),
            "messages": _strip_images(cap.get("messages") or []),
            "frames_attached": _candidate_frames(it, sh),
            "no_call_reason": None if cap else (res or {}).get("rationale"),
        })
    return out


def _candidate_frames(item: dict[str, Any], sh: dict[str, Any]) -> list[str]:
    """The frames `_judge_item` would attach for this item, in attach order.

    Mirrors its two image branches. Called "candidate" because `image_block`
    returns None for a frame it cannot read, and such a frame contributes no
    block — so this list is an upper bound on what was actually sent, aligned by
    index with the `image_ref` markers only when every frame encoded. The UI
    pairs them by index and shows the count of each, which makes a mismatch
    visible instead of silently mislabeling evidence.
    """
    pairs = (sh["pair_map"] or {}).get(normalize_item_id(item.get("check_item_id"))) \
        if _is_directional(item) else None
    if pairs:
        # DEFECT 67: mirror the burst branch's order — BEFORE, each DURING, AFTER
        # — under the same condition `_judge_item` uses, so the UI's index
        # pairing stays aligned with what was actually sent.
        _bf = (sh.get("burstframe_map") or {}).get(
            normalize_item_id(item.get("check_item_id"))) or []
        if _bf and len(_bf) == len(pairs):
            return [n for i, (pre, post) in enumerate(pairs)
                    for n in ([pre] + list(_bf[i]) + [post])]
        return [n for pre, post in pairs for n in (pre, post)]
    if str(item.get("mode", "")).startswith("VLM"):
        return list(sh["frame_files"])
    return []


def _strip_images(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Swap base64 image blocks for the frame name they were built from.

    `image_block` does not carry the source path through, so recover it
    positionally: the blocks are appended in the same order the frame names were
    iterated, and any frame that failed to encode contributed no block at all.
    Rather than guess a mapping that could mislabel evidence, mark each image
    with its index and let the caller pair it with the item's frame list.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        c = m.get("content")
        if not isinstance(c, list):
            out.append(m)
            continue
        blocks, n_img = [], 0
        for b in c:
            if isinstance(b, dict) and b.get("type") in ("image", "image_url"):
                blocks.append({"type": "image_ref", "index": n_img})
                n_img += 1
            else:
                blocks.append(b)
        out.append({**m, "content": blocks})
    return out
