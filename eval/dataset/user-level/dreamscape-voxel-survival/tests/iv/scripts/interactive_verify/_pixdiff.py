#!/usr/bin/env python3
"""Measured pixel change between a probe's before and after frames.

Why this exists. The accessibility tree is the judge's primary state evidence,
and for DOM widgets it is excellent. For a `<canvas>` it cannot work: a WebGL
scene that rotates, a particle field that thickens, a simulation that advances —
none of it touches the tree. The diff for such a step is therefore always
``(no accessibility-tree change)``, which `scorer` deliberately keeps as
evidence (for a DOM check, "nothing changed" IS the answer). Beside a weak
"the sphere looks slightly different" reading of two screenshots, that line
reads as a strong, confident negative, and the judge failed working artifacts on
it — b0811 `Eer4ptb` item 3, where the reviewer confirmed by eye that the
sphere, its specular highlights and the projected laser pattern all moved.

So for canvas-driven checks we supply the channel the AX tree cannot: how much
of the frame actually changed, measured. This is not a verdict — a moving
gradient and a correct simulation step look the same to it — it exists so the
judge can tell "the artifact did nothing" from "the artifact changed and the
tree is simply the wrong instrument".

Deliberately coarse: an 8-bit grayscale downscale to at most 256px wide, then
the fraction of pixels whose value moved by more than a noise floor. JPEG
artefacts and 1-LSB dithering must not read as motion, and a 256px thumbnail
of a 1280x720 frame still resolves a slider handle moving or a scene rotating.
"""
from __future__ import annotations

import os
from typing import Any

# Per-pixel change below this (0-255) is encoder noise / dithering, not motion.
NOISE_FLOOR = int(os.environ.get("IV_PIXDIFF_FLOOR", "8"))
# Working width. Small enough that the compare costs ~1ms, large enough that a
# slider handle (~14px of 1280 => ~3px here) still registers.
WORK_W = int(os.environ.get("IV_PIXDIFF_WIDTH", "256"))


def _load_gray(path: str) -> Any:
    """Downscaled 8-bit grayscale array for `path`, or None."""
    try:
        from PIL import Image
        import numpy as np
    except ImportError:  # Pillow/numpy are not hard deps of this pipeline
        return None
    try:
        im = Image.open(path)
        im.load()
        im = im.convert("L")
        w, h = im.size
        if w > WORK_W:
            im = im.resize((WORK_W, max(1, round(h * WORK_W / w))), Image.BILINEAR)
        return np.asarray(im, dtype="int16")
    except Exception:  # noqa: BLE001 - missing/truncated frame
        return None


def frame_delta(before_path: str, after_path: str) -> dict[str, float] | None:
    """{changed_frac, mean_abs, max_abs} between two frames, or None.

    None means "could not measure" (a frame missing, sizes disagree, Pillow
    absent) and callers must treat it as no evidence rather than as no change —
    the distinction this whole pipeline turns on.
    """
    a, b = _load_gray(before_path), _load_gray(after_path)
    if a is None or b is None or a.shape != b.shape or a.size == 0:
        return None
    try:
        import numpy as np
        d = np.abs(b - a)
        return {"changed_frac": round(float((d > NOISE_FLOOR).mean()), 4),
                "mean_abs": round(float(d.mean()), 2),
                "max_abs": float(d.max())}
    except Exception:  # noqa: BLE001
        return None


def describe(delta: dict[str, float] | None) -> str:
    """One line for the judge prompt. Empty string when unmeasurable."""
    if not delta:
        return ""
    pct = delta["changed_frac"] * 100.0
    if delta["changed_frac"] <= 0.001:
        verdict = ("the rendered image is IDENTICAL before and after — nothing "
                   "on screen moved or redrew")
    elif delta["changed_frac"] < 0.01:
        verdict = ("a small part of the image changed (a local update such as a "
                   "readout, a handle moving, or one element redrawing)")
    else:
        verdict = ("a substantial part of the image changed — the artifact "
                   "visibly redrew in response")
    return (f"MEASURED PIXEL CHANGE between this check's BEFORE and AFTER "
            f"frames: {pct:.2f}% of pixels changed (mean intensity delta "
            f"{delta['mean_abs']}, max {delta['max_abs']:g}) — {verdict}.")


# A consecutive-frame change at or below this reads as a static image. Above the
# single-frame noise floor's equivalent, and deliberately generous: a 3D scene
# idling still dithers a little, and calling that "animating" would make the
# pause signal useless.
MOTION_FLOOR = float(os.environ.get("IV_MOTION_FLOOR", "0.002"))


def burst_timeline(paths: list[str]) -> list[dict[str, float]] | None:
    """Frame-to-frame deltas across a burst, or None if unmeasurable.

    DEFECT 67. `frame_delta` answers "did the endpoints differ", which cannot
    distinguish a Pause that works from a Pause that does nothing: a paused
    animation is still one tick further along than it was when the click landed,
    so the endpoints differ either way (the user's "the screenshots before and
    after pausing are indeed different"). The distinguishing evidence is whether
    consecutive frames stop differing from EACH OTHER, and that needs three or
    more samples.

    Returns one delta per consecutive pair (so N frames -> N-1 entries). Fewer
    than two readable frames yields None — no evidence, never "no motion".
    """
    if not paths or len(paths) < 2:
        return None
    out: list[dict[str, float]] = []
    for a, b in zip(paths, paths[1:]):
        d = frame_delta(a, b)
        if d is not None:
            out.append(d)
    return out or None


def describe_timeline(deltas: list[dict[str, float]] | None) -> str:
    """One block for the judge prompt describing motion over the burst.

    States the per-interval measurements and then the one conclusion they
    support that a before/after pair cannot: whether the artifact was STILL
    CHANGING at the end of the window, or had come to rest. It stops there
    deliberately — "still moving" is not "moving correctly", and only the check
    itself says which of those was asked for.
    """
    if not deltas:
        return ""
    fracs = [d["changed_frac"] for d in deltas]
    series = ", ".join(f"{f * 100.0:.2f}%" for f in fracs)
    moving = [f > MOTION_FLOOR for f in fracs]
    if not any(moving):
        tail = ("the image is STATIC across the whole window — consecutive "
                "frames are identical, so nothing was animating or in motion "
                "during it")
    elif all(moving):
        tail = ("the image is STILL CHANGING at the end of the window — it was "
                "in continuous motion throughout, not settling")
    elif moving[0] and not moving[-1]:
        tail = ("the image was changing at the start of the window and had come "
                "to REST by its end — motion stopped, or a transition finished "
                "and settled")
    elif moving[-1]:
        tail = ("the image was static at the start of the window and was MOVING "
                "by its end — motion started during it")
    else:
        tail = "the image changed intermittently across the window"
    return (f"MEASURED MOTION DURING THIS CHECK'S OBSERVATION WINDOW "
            f"({len(deltas) + 1} frames sampled across it, change between each "
            f"consecutive pair): {series} — {tail}.\n"
            f"This measures MOTION, not correctness. It answers 'was the "
            f"artifact animating/still moving', which the BEFORE/AFTER pair "
            f"cannot: a paused animation still differs from its pre-pause frame "
            f"by one tick, so a non-zero BEFORE-vs-AFTER change is NOT evidence "
            f"that a pause failed. For a check about starting, stopping, "
            f"pausing, or continuous motion, judge it on this line.")
