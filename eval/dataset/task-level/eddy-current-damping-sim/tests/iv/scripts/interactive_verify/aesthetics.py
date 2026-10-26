#!/usr/bin/env python3
"""Stage 3b — score an artifact's AESTHETICS from captured screenshots.

Independent of the functional checklist score. Implements the 5-dimension,
0–4-per-dimension rubric in ``scripts/style_catalog/aesthetics_detail_en.md``
(layout, color, typography, craft, interaction; 20 max) — that file is the
authored source of the band descriptions and per-dimension checks in
``RUBRIC_SYS``, and is the thing to edit first if the bands need to change.
``docs/aesthetics_grading_rubric_zh.md`` is the earlier, coarser statement of
the same five dimensions.

Two of the authored checks are deliberately RESTATED rather than copied. The
detail doc tells a human reviewer to read contrast ratios and computed
font-size/line-height out of DevTools; this judge only ever receives PNGs, so
those become explicit visual estimates with the numeric standard named as the
target being estimated. A check phrased as an instruction the judge cannot
carry out does not raise the bar — it just becomes decorative text that invites
a fabricated measurement.

A single VLM call sees the desktop shot, interaction keyframes, and (only if the
task needed it) the mobile shot, and returns per-dimension scores + one
evidence-citing reason each + flags.

Never raises into the batch: any judge/parse failure returns a safe all-None
block flagged ``judge_error`` (mirrors ``scorer._judge_item``).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from scripts.interactive_verify._images import image_block

DIMENSIONS = ("layout", "color", "typography", "craft", "interaction")

FLAGS = frozenset({
    "render_fail", "color_contrast_below_AA", "default_widgets_only",
    "mixed_icon_sets", "mobile_overflow", "interaction_frames_missing",
    "judge_error",
})

MAX_INTERACTION_FRAMES = 6

# Which rubric produced a score. Stamped onto every block and bumped whenever
# RUBRIC_SYS changes in a way that moves scores, because the aesthetics score is
# NOT comparable across rubrics: under v1 (five one-line dimension gists, no band
# descriptions) the judge invented its own scale and 50.5% of 1,042 verdicts
# landed on exactly 100/100. Pooling those with v2 scores would average two
# different measuring instruments and read as a quality shift in the artifacts.
#
# It also makes the backfill idempotent -- _rescore_aesthetics.py re-scores
# exactly the blocks whose stamp is not current -- and a long sweep survives a
# mid-flight rubric change without silently mixing scales, since a running
# driver holds the module it imported at launch.
RUBRIC_VERSION = 2

RUBRIC_SYS = (
    "You are a strict senior frontend design reviewer. You grade ONLY visual and "
    "aesthetic quality (NOT whether features work — that is scored separately). "
    "Grade CRAFTSMANSHIP, not trendiness: a bold, dense, or unusual design that is "
    "executed cleanly scores HIGH; a 'safe' design with misaligned elements, poor "
    "contrast, or all-default styling scores LOW. Do NOT reward a page for merely "
    "looking minimal or trendy.\n\n"
    "You are given screenshots: a desktop full-page shot, optional interaction "
    "keyframes (hover/click/modal), and — only when the task required mobile "
    "support — a mobile (390x844) shot. Score FIVE dimensions, each an integer 0–4.\n\n"
    "METHOD, for every dimension: run that dimension's CHECKS first, then pick the "
    "band whose description matches what you found. Do not pick a band first and "
    "justify it afterwards. If your findings fall BETWEEN two bands, take the LOWER "
    "band and say in the reason which check held it down.\n\n"

    "D1. layout — composition, alignment, spacing, visual flow.\n"
    "  CHECKS: Are elements aligned to each other? Do the gaps look consistent, or "
    "do they visibly vary? Any unintended overlap, or content clipped/cut off? Is "
    "the primary action or main content visually prominent?\n"
    "  0 elements overlap, content is clipped, or the interface is not meaningfully "
    "distinguishable.\n"
    "  1 some structure, but alignment is inconsistent and whitespace is cramped or "
    "messy.\n"
    "  2 a recognizable grid with minor alignment/spacing issues; some hierarchy, but "
    "it feels flat.\n"
    "  3 consistent grid, clear hierarchy, whitespace looks intentional; only minor "
    "imperfections.\n"
    "  4 precise alignment, consistently balanced spacing, and a clear, deliberately "
    "guided visual flow.\n\n"

    "D2. color — palette harmony, coherence, and text legibility.\n"
    "  CHECKS: Is the main palette harmonious, generally no more than three main "
    "colors counted visually (neutrals included)? Is body text comfortable to read "
    "against its background? Do interactive elements share one consistent accent "
    "color? You cannot measure a contrast ratio from a screenshot — judge legibility "
    "by eye, and treat WCAG AA (about 4.5:1 for normal text, 3:1 for large text) as "
    "the standard you are estimating against, not a number to report.\n"
    "  0 colors clash or look muddy; text is hard or impossible to read against its "
    "background.\n"
    "  1 dull or inconsistent scheme; some text has insufficient contrast.\n"
    "  2 safe, practical, harmonious palette that lacks distinctiveness; contrast is "
    "generally adequate.\n"
    "  3 intentional, harmonious palette that guides attention; body text looks to "
    "meet AA.\n"
    "  4 distinctive, cohesive, purposefully applied palette; all text and UI "
    "elements look to meet contrast standards.\n\n"

    "D3. typography — font choice, sizing, hierarchy, line-height rhythm.\n"
    "  CHECKS: Is there a clear size/weight hierarchy between headings and body? Does "
    "body text look comfortably sized with comfortable line spacing? Is the font "
    "choice deliberate and consistent? Judge all of this visually — you cannot read "
    "computed styles.\n"
    "  0 illegible or visually jarring; font sizes look arbitrary.\n"
    "  1 basic or inconsistent; readable but unrefined.\n"
    "  2 clean and readable but generic; weak hierarchy.\n"
    "  3 well-chosen fonts, clear heading/body hierarchy, comfortable rhythm.\n"
    "  4 a professional type system is evident: intentional scale, excellent "
    "readability, hierarchy, and rhythm.\n\n"

    "D4. craft — quality, consistency, and refinement of the actual components.\n"
    "  CHECKS: Is component styling consistent across the page (border radius, "
    "shadows, states)? Do the icons look like one icon set / one visual language? Are "
    "images and other assets sharp, and free of stretching, distortion, or heavy "
    "blur?\n"
    "  0 default/unstyled controls, or visual assets are broken or severely blurred.\n"
    "  1 very little styling; component or icon styles are inconsistent.\n"
    "  2 standard components with a consistent visual style; assets are acceptable.\n"
    "  3 polished, cohesive components with appropriate states, radii, and shadows; "
    "assets are clear.\n"
    "  4 a refined, highly cohesive component system; every element looks deliberate.\n\n"

    "D5. interaction — interaction states and responsive polish.\n"
    "  CHECKS: Do interactive elements show visible hover, pressed/active, and focus "
    "states? Do transitions look smooth rather than abrupt or stuttering — judge this "
    "VISUALLY from the keyframes; never estimate a duration in milliseconds. If a "
    "mobile shot is present: any horizontal scrolling, overlap, or layout collapse?\n"
    "  0 no meaningful interaction feedback; animations broken or visibly stuttering; "
    "or the mobile layout collapses.\n"
    "  1 only default interaction behavior; mobile is usable but rough.\n"
    "  2 basic hover/pressed feedback; mobile layout is generally complete.\n"
    "  3 smooth transitions, clear hover/pressed/focus states, clean and well-composed "
    "mobile layout.\n"
    "  4 delightful, purposeful micro-interactions and exceptionally polished "
    "responsive behavior.\n\n"

    "HARD RULES (apply before choosing a tier):\n"
    "  * If the page is blank / errored / core content missing or broken: set "
    "render_ok=false and ALL five scores to 0, add flag 'render_fail'.\n"
    "  * If body text needs squinting to read (low contrast): color is AT MOST 1; "
    "add flag 'color_contrast_below_AA'.\n"
    "  * If the page is mostly default browser controls and/or unstyled solid-color "
    "blocks, with little or no evidence of deliberate design effort: craft is AT MOST "
    "1; add flag 'default_widgets_only'.\n"
    "  * If icons visibly come from more than one icon set or visual language (mixed "
    "stroke weights, mixed filled/outline styles, mismatched icon corner radii): add "
    "flag 'mixed_icon_sets'. Do not apply a numeric cap for this — it is a D4 check, "
    "so let it pull your craft band down through the band descriptions above "
    "('component or icon styles are inconsistent' is band 1; a cohesive component "
    "system in bands 3–4 is not compatible with a mixed icon language).\n"
    "  * When you are between two tiers, choose the LOWER one.\n"
    "  * If a mobile shot is present and the mobile layout overflows/overlaps/"
    "collapses, add flag 'mobile_overflow'.\n"
    "  * If interaction keyframes are absent, do NOT penalize hover/press states you "
    "cannot see; judge interaction from the static + (if present) mobile shots and "
    "add flag 'interaction_frames_missing'. NEVER add 'mobile_overflow' when no "
    "mobile shot is present.\n\n"
    "Each reason MUST name a concrete element (e.g. 'the primary CTA button', 'the "
    "card grid', 'the footer') — never vague praise like 'looks nice'.\n\n"
    "Respond with ONLY this JSON object:\n"
    '{"render_ok": true|false, '
    '"scores": {"layout":0-4,"color":0-4,"typography":0-4,"craft":0-4,"interaction":0-4}, '
    '"reasons": {"layout":"...","color":"...","typography":"...","craft":"...","interaction":"..."}, '
    '"flags": ["..."]}'
)


def _safe_block(flag: str, reason: str) -> dict[str, Any]:
    """A never-raise fallback verdict: all dims unknown, math None."""
    return {
        "render_ok": None,
        "scores": {d: None for d in DIMENSIONS},
        "reasons": {d: reason for d in DIMENSIONS},
        "flags": [flag],
        "total_20": None,
        "total_100": None,
        "rubric_version": RUBRIC_VERSION,
    }


def _collect_frames(evidence: dict[str, Any], frames_dir: Path) -> list[str]:
    """Ordered frame basenames to attach: desktop, capped interaction, mobile."""
    ordered: list[str] = []
    if evidence.get("desktop"):
        ordered.append(evidence["desktop"])
    inter = list(evidence.get("interaction") or [])[:MAX_INTERACTION_FRAMES]
    ordered.extend(inter)
    if evidence.get("mobile"):
        ordered.append(evidence["mobile"])
    return ordered


def _normalize(obj: dict[str, Any], *, has_mobile: bool) -> dict[str, Any]:
    """Clamp scores to 0–4 ints, enforce hard rules, compute totals, filter flags."""
    render_ok = bool(obj.get("render_ok", True))
    raw_scores = obj.get("scores") or {}
    scores: dict[str, Any] = {}
    for d in DIMENSIONS:
        v = raw_scores.get(d)
        try:
            iv = int(round(float(v)))
        except (TypeError, ValueError):
            iv = None
        if iv is not None:
            iv = max(0, min(4, iv))
        scores[d] = iv
    reasons = {d: str((obj.get("reasons") or {}).get(d, "")) for d in DIMENSIONS}
    flags = [f for f in (obj.get("flags") or []) if f in FLAGS]

    if not render_ok:
        scores = {d: 0 for d in DIMENSIONS}
        if "render_fail" not in flags:
            flags.append("render_fail")
    # never report mobile_overflow when we did not show a mobile shot
    if not has_mobile and "mobile_overflow" in flags:
        flags = [f for f in flags if f != "mobile_overflow"]

    if any(scores[d] is None for d in DIMENSIONS):
        total_20 = total_100 = None
    else:
        total_20 = sum(scores[d] for d in DIMENSIONS)
        total_100 = round(total_20 / 20 * 100)
    return {"render_ok": render_ok, "scores": scores, "reasons": reasons,
            "flags": flags, "total_20": total_20, "total_100": total_100,
            "rubric_version": RUBRIC_VERSION}


def score_aesthetics(evidence: dict[str, Any], code: str, vlm_call: Callable, *,
                     frames_dir: Path, needs_mobile: bool) -> dict[str, Any]:
    frames_dir = Path(frames_dir)
    names = _collect_frames(evidence, frames_dir)
    has_mobile = bool(evidence.get("mobile"))
    header = (
        "Grade the aesthetics of this artifact from the screenshots below.\n"
        f"Mobile support {'WAS' if needs_mobile else 'was NOT'} required by the task"
        + (" (a mobile shot is attached)." if has_mobile else
           " (no mobile shot attached).")
    )
    blocks: list[dict[str, Any]] = [{"type": "text", "text": header}]
    attached = 0
    for name in names:
        blk = image_block(str(frames_dir / name))
        if blk is not None:
            blocks.append(blk)
            attached += 1
    if attached == 0:
        # nothing renderable to look at
        return _safe_block("render_fail", "no screenshots were captured")
    messages = [{"role": "system", "content": RUBRIC_SYS},
                {"role": "user", "content": blocks}]
    try:
        obj = vlm_call(messages)
    except Exception as exc:  # noqa: BLE001 — never raise into the batch
        return _safe_block("judge_error", f"aesthetics judge error: {exc}"[:200])
    if not isinstance(obj, dict):
        return _safe_block("judge_error", "aesthetics judge returned non-object")
    # _normalize must stay inside the guard: a malformed-but-dict return (scores as
    # a list, flags as an int, ...) would otherwise raise past the never-raise
    # boundary and kill the batch. On any such failure, degrade to a judge_error
    # block so the analysis harness still counts it.
    try:
        out = _normalize(obj, has_mobile=has_mobile)
        if not evidence.get("interaction") and "interaction_frames_missing" not in out["flags"]:
            out["flags"].append("interaction_frames_missing")
    except Exception as exc:  # noqa: BLE001 — never raise into the batch
        return _safe_block("judge_error", f"aesthetics parse error: {exc}"[:200])
    return out
