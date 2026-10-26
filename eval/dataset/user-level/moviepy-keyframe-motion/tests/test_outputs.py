"""Verifier for the moviepy keyframe animation feature.

All expected values below are frozen constants derived from the public
behavioural contract of the feature (Penner easing formulas, keyframe
interpolation rules, and the exact pixel geometry of the motion effect).
Nothing is recomputed from the state of the repository under test; the
repository only provides the implementation being checked.

This file covers three families of behaviour:

    1. The new ``moviepy.easing`` module, ``moviepy.KeyframeTrack`` class,
       and ``moviepy.vfx.KeyframeMotion`` effect.

    2. Deep integration of ``KeyframeTrack`` with the pre-existing
       effects and clip methods (``vfx.Resize``, ``vfx.Rotate``,
       ``vfx.MultiplyColor``, ``VideoClip.with_position``,
       ``VideoClip.with_opacity``). All of these now accept a
       ``KeyframeTrack`` in addition to their historical value/callable
       forms.

    3. Backward-compatibility regressions: the historical static-value
       and ``lambda t: ...`` code paths for the effects above are still
       observable byte-for-byte, so ``KeyframeTrack`` support does not
       silently reshape the old behaviour.
"""

from __future__ import annotations

import math
import sys

import numpy as np
import pytest

if "/app" not in sys.path:
    sys.path.insert(0, "/app")


# --------------------------------------------------------------------- data

# A 4x4 frame with four colored quadrants: TL red, TR green, BL blue,
# BR yellow. Frozen test input.
QUADRANT = np.array(
    [
        [[255, 0, 0], [255, 0, 0], [0, 255, 0], [0, 255, 0]],
        [[255, 0, 0], [255, 0, 0], [0, 255, 0], [0, 255, 0]],
        [[0, 0, 255], [0, 0, 255], [255, 255, 0], [255, 255, 0]],
        [[0, 0, 255], [0, 0, 255], [255, 255, 0], [255, 255, 0]],
    ],
    dtype=np.uint8,
)

QUADRANT_ROT90 = np.array(
    [
        [[0, 255, 0], [0, 255, 0], [255, 255, 0], [255, 255, 0]],
        [[0, 255, 0], [0, 255, 0], [255, 255, 0], [255, 255, 0]],
        [[255, 0, 0], [255, 0, 0], [0, 0, 255], [0, 0, 255]],
        [[255, 0, 0], [255, 0, 0], [0, 0, 255], [0, 0, 255]],
    ],
    dtype=np.uint8,
)

EASING_NAMES = [
    "ease_in_back",
    "ease_in_bounce",
    "ease_in_circ",
    "ease_in_cubic",
    "ease_in_elastic",
    "ease_in_expo",
    "ease_in_out_back",
    "ease_in_out_bounce",
    "ease_in_out_circ",
    "ease_in_out_cubic",
    "ease_in_out_elastic",
    "ease_in_out_expo",
    "ease_in_out_quad",
    "ease_in_out_quart",
    "ease_in_out_quint",
    "ease_in_out_sine",
    "ease_in_quad",
    "ease_in_quart",
    "ease_in_quint",
    "ease_in_sine",
    "ease_out_back",
    "ease_out_bounce",
    "ease_out_circ",
    "ease_out_cubic",
    "ease_out_elastic",
    "ease_out_expo",
    "ease_out_quad",
    "ease_out_quart",
    "ease_out_quint",
    "ease_out_sine",
    "hold",
    "linear",
]

# Frozen samples of representative easings, computed once from the
# documented Penner formulas at p = 0.1 .. 0.9.
FROZEN_EASING_SAMPLES = {
    "linear": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9],
    "hold": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "ease_in_quad": [0.01, 0.04, 0.09, 0.16, 0.25, 0.36, 0.49, 0.64, 0.81],
    "ease_out_quad": [0.19, 0.36, 0.51, 0.64, 0.75, 0.84, 0.91, 0.96, 0.99],
    "ease_in_cubic": [0.001, 0.008, 0.027, 0.064, 0.125, 0.216, 0.343, 0.512, 0.729],
    "ease_out_bounce": [0.075625, 0.3025, 0.680625, 0.91, 0.765625, 0.7725, 0.930625, 0.94, 0.988125],
    "ease_in_back": [-0.01431422, -0.04645056, -0.08019954, -0.09935168, -0.0876975, -0.02902752, 0.09286774, 0.29419776, 0.59117202],
    "ease_in_out_cubic": [0.004, 0.032, 0.108, 0.256, 0.5, 0.744, 0.892, 0.968, 0.996],
    "ease_in_out_sine": [0.0244717418524, 0.0954915028125, 0.206107373854, 0.345491502813, 0.5, 0.654508497187, 0.793892626146, 0.904508497187, 0.975528258148],
    "ease_in_out_quint": [0.00016, 0.00512, 0.03888, 0.16384, 0.5, 0.83616, 0.96112, 0.99488, 0.99984],
}


def static_clip(frame, duration=2.0):
    from moviepy import VideoClip

    frame = np.ascontiguousarray(frame)
    return VideoClip(frame_function=lambda t: frame.copy(), duration=duration)


# ------------------------------------------------------------- public API


def test_top_level_imports():
    import moviepy

    assert isinstance(moviepy.KeyframeTrack, type)
    assert hasattr(moviepy, "easing")
    assert isinstance(moviepy.vfx.KeyframeMotion, type)


def test_keyframe_motion_is_a_video_effect():
    from moviepy import Effect, vfx

    assert issubclass(vfx.KeyframeMotion, Effect)


# ------------------------------------------------------------------ easing


def test_easing_registry_exact_names():
    from moviepy.easing import EASINGS

    assert sorted(EASINGS) == EASING_NAMES
    assert len(EASINGS) == 32
    for name in EASING_NAMES:
        assert callable(EASINGS[name])


def test_frozen_easing_samples():
    from moviepy.easing import EASINGS

    points = [i / 10.0 for i in range(1, 10)]
    for name, expected in FROZEN_EASING_SAMPLES.items():
        actual = [EASINGS[name](p) for p in points]
        assert actual == pytest.approx(expected, abs=1e-9), name


def test_easing_endpoints():
    from moviepy.easing import EASINGS

    for name in EASING_NAMES:
        func = EASINGS[name]
        assert func(0.0) == pytest.approx(0.0, abs=1e-12), name
        assert func(1.0) == pytest.approx(1.0, abs=1e-12), name


def test_hold_easing_steps():
    from moviepy.easing import hold

    assert hold(0.0) == 0.0
    assert hold(0.999) == 0.0
    assert hold(1.0) == 1.0


def test_in_out_symmetry():
    from moviepy.easing import EASINGS

    for name in EASING_NAMES:
        if not name.startswith("ease_in_out_"):
            continue
        func = EASINGS[name]
        assert func(0.5) == pytest.approx(0.5, abs=1e-9), name
        for i in range(1, 10):
            p = i / 10.0
            assert func(p) == pytest.approx(1.0 - func(1.0 - p), abs=1e-9), name


def test_in_out_mirror_of_in_and_out():
    """f_in(p) == 1 - f_out(1 - p) for every polynomial / trig family."""
    from moviepy.easing import EASINGS

    pairs = [
        ("quad",), ("cubic",), ("quart",), ("quint",),
        ("sine",), ("expo",), ("circ",), ("back",),
        ("elastic",), ("bounce",),
    ]
    for (family,) in pairs:
        f_in = EASINGS["ease_in_" + family]
        f_out = EASINGS["ease_out_" + family]
        for i in range(21):
            p = i / 20.0
            assert f_in(p) == pytest.approx(1.0 - f_out(1.0 - p), abs=1e-9), (
                family, p,
            )


def test_expo_exact_endpoint_special_cases():
    from moviepy.easing import ease_in_expo, ease_out_expo, ease_in_out_expo

    assert ease_in_expo(0.0) == 0.0
    assert ease_out_expo(1.0) == 1.0
    assert ease_in_out_expo(0.0) == 0.0
    assert ease_in_out_expo(1.0) == 1.0
    # Just inside the domain the formulas should be pure powers of two.
    assert ease_in_expo(0.1) == pytest.approx(2.0 ** -9)
    assert ease_out_expo(0.9) == pytest.approx(1.0 - 2.0 ** -9)


def test_elastic_exact_endpoints_and_overshoot():
    from moviepy.easing import (
        ease_in_elastic,
        ease_out_elastic,
        ease_in_out_elastic,
    )

    assert ease_in_elastic(0.0) == 0.0
    assert ease_in_elastic(1.0) == 1.0
    assert ease_out_elastic(0.0) == 0.0
    assert ease_out_elastic(1.0) == 1.0
    assert ease_in_out_elastic(0.0) == 0.0
    assert ease_in_out_elastic(1.0) == 1.0
    # elastic must overshoot in the middle of the domain
    assert max(ease_out_elastic(i / 100.0) for i in range(1, 100)) > 1.0
    assert min(ease_in_elastic(i / 100.0) for i in range(1, 100)) < 0.0


def test_get_easing_resolution():
    from moviepy import easing as easing_module
    from moviepy.easing import get_easing

    assert get_easing("linear") is easing_module.linear
    custom = lambda p: p * 0.5  # noqa: E731
    assert get_easing(custom) is custom
    with pytest.raises(ValueError):
        get_easing("ease_sideways")
    with pytest.raises(TypeError):
        get_easing(42)


# ------------------------------------------------------------- KeyframeTrack


def test_track_linear_interpolation_and_clamping():
    from moviepy import KeyframeTrack

    track = KeyframeTrack([(0.0, 0.0), (2.0, 100.0)])
    assert track.value_at(0.5) == pytest.approx(25.0)
    assert track.value_at(1.0) == pytest.approx(50.0)
    assert track.value_at(-1.0) == 0.0
    assert track.value_at(99.0) == 100.0
    assert track.times == (0.0, 2.0)
    assert track.values == (0.0, 100.0)
    assert track.duration == 2.0
    assert track.is_tuple is False
    assert len(track) == 2


def test_track_easing_applied_to_segment_progress():
    from moviepy import KeyframeTrack

    track = KeyframeTrack([(0.0, 0.0), (2.0, 100.0)], easing="ease_in_quad")
    assert track.value_at(1.0) == pytest.approx(25.0)
    assert track.value_at(0.5) == pytest.approx(6.25)


def test_track_hold_easing():
    from moviepy import KeyframeTrack

    track = KeyframeTrack([(0.0, 0.0), (1.0, 10.0), (2.0, 20.0)], easing="hold")
    assert track.value_at(0.999) == 0.0
    assert track.value_at(1.0) == 10.0
    assert track.value_at(1.5) == 10.0
    assert track.value_at(2.0) == 20.0


def test_track_per_segment_interpolation():
    from moviepy import KeyframeTrack

    track = KeyframeTrack([(0.0, 0.0), (1.0, 10.0), (3.0, 50.0)])
    # first segment: 0 -> 10 over [0, 1]
    assert track.value_at(0.5) == pytest.approx(5.0)
    # second segment: 10 -> 50 over [1, 3]
    assert track.value_at(2.0) == pytest.approx(30.0)


def test_track_tuple_interpolation():
    from moviepy import KeyframeTrack

    track = KeyframeTrack([(0.0, (0.0, 100.0)), (2.0, (10.0, 200.0))])
    assert track.is_tuple is True
    assert track.value_at(1.0) == pytest.approx((5.0, 150.0))
    assert track.value_at(-3.0) == (0.0, 100.0)
    assert track.value_at(7.0) == (10.0, 200.0)


def test_track_repr_mentions_class_name():
    from moviepy import KeyframeTrack

    text = repr(KeyframeTrack([(0.0, 1.0), (1.0, 2.0)]))
    assert "KeyframeTrack" in text


def test_track_construction_errors():
    from moviepy import KeyframeTrack

    with pytest.raises(ValueError):
        KeyframeTrack([])
    with pytest.raises(ValueError):
        KeyframeTrack([(1.0, 0.0), (0.5, 1.0)])
    with pytest.raises(ValueError):
        KeyframeTrack([(-1.0, 0.0), (1.0, 1.0)])
    # 2026-09-19 QC fix: instruction.md and the s_ctor_errors info slice allow
    # TypeError OR ValueError for inconsistent value types; only negative times
    # are pinned to ValueError. Accept either so spec-compliant TypeError
    # implementations are not wrongly failed (consistent with :651/:748/:839).
    with pytest.raises((TypeError, ValueError)):
        KeyframeTrack([(0.0, 1.0), (1.0, (1.0, 2.0))])
    with pytest.raises((TypeError, ValueError)):
        KeyframeTrack([(0.0, (1.0, 2.0)), (1.0, (1.0, 2.0, 3.0))])
    with pytest.raises(ValueError):
        KeyframeTrack([(0.0, 0.0), (1.0, 1.0)], easing="ease_sideways")
    with pytest.raises(TypeError):
        KeyframeTrack([(0.0, "fast")])
    with pytest.raises(TypeError):
        KeyframeTrack([(True, 1.0)])
    with pytest.raises(TypeError):
        KeyframeTrack(42)
    with pytest.raises(TypeError):
        KeyframeTrack([(0.0, 0.0), (1.0, 1.0)], easing=42)


def test_track_factories():
    from moviepy import KeyframeTrack

    const = KeyframeTrack.constant(3.5)
    assert const.times == (0.0,)
    assert const.value_at(10.0) == 3.5

    ramp = KeyframeTrack.ramp(0.0, 100.0, 2.0)
    assert ramp.times == (0.0, 2.0)
    assert ramp.value_at(1.0) == pytest.approx(50.0)

    ramp_tuple = KeyframeTrack.ramp((0, 0), (10, 20), 1.0)
    assert ramp_tuple.value_at(0.5) == pytest.approx((5.0, 10.0))

    with pytest.raises(ValueError):
        KeyframeTrack.ramp(0.0, 1.0, 0.0)
    with pytest.raises(ValueError):
        KeyframeTrack.ramp(0.0, 1.0, -1.0)
    with pytest.raises(TypeError):
        KeyframeTrack.ramp(0.0, 1.0, "later")


def test_track_transforms():
    from moviepy import KeyframeTrack

    track = KeyframeTrack([(1.0, 1.0), (3.0, 3.0)])

    scaled = track.rescaled(2.0)
    assert scaled.values == (2.0, 6.0)
    assert scaled.times == track.times

    shifted = track.shifted(2.0)
    assert shifted.times == (3.0, 5.0)
    with pytest.raises(ValueError):
        track.shifted(-2.0)

    reversed_track = track.reversed()
    assert reversed_track.times == (0.0, 2.0)
    assert reversed_track.values == (3.0, 1.0)
    assert reversed_track.value_at(0.5) == pytest.approx(2.5)

    # originals are untouched
    assert track.times == (1.0, 3.0)
    assert track.values == (1.0, 3.0)


def test_track_transforms_compose():
    from moviepy import KeyframeTrack

    track = KeyframeTrack([(0.0, 1.0), (2.0, 3.0)])
    result = track.rescaled(2.0).shifted(1.0).reversed()
    assert result.times == (0.0, 2.0)
    assert result.values == (6.0, 2.0)
    # the original is untouched
    assert track.times == (0.0, 2.0)
    assert track.values == (1.0, 3.0)


def test_track_value_at_rejects_non_numeric_time():
    from moviepy import KeyframeTrack

    track = KeyframeTrack([(0.0, 0.0), (1.0, 1.0)])
    with pytest.raises(TypeError):
        track.value_at("0.5")


def test_track_accepts_numpy_scalars():
    np_ = pytest.importorskip("numpy")
    from moviepy import KeyframeTrack

    track = KeyframeTrack(
        [(np_.float64(0.0), np_.int64(1)), (np_.float64(1.0), np_.float32(2.0))]
    )
    assert track.value_at(np_.float64(0.5)) == pytest.approx(1.5)


def test_track_reversed_preserves_easing_identity():
    """``reversed()`` must reuse the same easing callable (not a mirror)."""
    from moviepy import KeyframeTrack
    from moviepy.easing import ease_in_quad

    track = KeyframeTrack([(0.0, 0.0), (2.0, 100.0)], easing="ease_in_quad")
    reversed_track = track.reversed()
    assert reversed_track.easing is ease_in_quad
    # With ease_in_quad and a reversed track [(0, 100), (2, 0)] the value
    # at t=1 uses eased progress 0.25 -> value 100 + (0-100)*0.25 = 75.
    assert reversed_track.value_at(1.0) == pytest.approx(75.0)


# ---------------------------------------------------------- KeyframeMotion


def test_identity_effect_preserves_everything():
    from moviepy import vfx

    clip = static_clip(QUADRANT, duration=3.0).with_fps(12)
    out = clip.with_effects([vfx.KeyframeMotion()])
    assert out.size == (4, 4)
    assert out.duration == 3.0
    assert out.fps == 12
    assert np.array_equal(out.get_frame(0.0), QUADRANT)
    assert np.array_equal(out.get_frame(1.3), QUADRANT)
    # source clip untouched
    assert np.array_equal(clip.get_frame(0.0), QUADRANT)


def test_canvas_size_changes_output_size():
    from moviepy import vfx

    clip = static_clip(QUADRANT)
    out = clip.with_effects([vfx.KeyframeMotion(canvas_size=(10, 6))])
    assert out.size == (10, 6)
    assert out.get_frame(1.0).shape == (6, 10, 3)


def test_opacity_exact_pixels():
    from moviepy import ColorClip, vfx

    cases = [
        ((200, 100, 48), 1.0, (200, 100, 48)),
        ((200, 100, 48), 0.5, (100, 50, 24)),
        ((200, 100, 48), 0.25, (50, 25, 12)),
        ((255, 255, 255), 0.2, (51, 51, 51)),
        ((255, 255, 255), 0.0, (0, 0, 0)),
    ]
    for color, opacity, expected in cases:
        clip = ColorClip(size=(2, 2), color=color, duration=1.0)
        out = clip.with_effects([vfx.KeyframeMotion(opacity=opacity)])
        pixel = tuple(int(v) for v in out.get_frame(0.0)[0, 0])
        assert pixel == expected, f"color={color} opacity={opacity}"


def test_animated_opacity_track():
    from moviepy import ColorClip, KeyframeTrack, vfx

    clip = ColorClip(size=(2, 2), color=(200, 100, 48), duration=2.0)
    track = KeyframeTrack([(0.0, 1.0), (2.0, 0.5)])
    out = clip.with_effects([vfx.KeyframeMotion(opacity=track)])
    assert list(out.get_frame(0.0)[0, 0]) == [200, 100, 48]
    assert list(out.get_frame(1.0)[0, 0]) == [150, 75, 36]
    assert list(out.get_frame(2.0)[0, 0]) == [100, 50, 24]


def test_rotation_90_counterclockwise():
    from moviepy import vfx

    out = static_clip(QUADRANT).with_effects([vfx.KeyframeMotion(rotation=90.0)])
    assert np.array_equal(out.get_frame(0.0), QUADRANT_ROT90)


def test_rotation_180_and_360():
    from moviepy import vfx

    expected_180 = np.array(
        [
            [[255, 255, 0], [255, 255, 0], [0, 0, 255], [0, 0, 255]],
            [[255, 255, 0], [255, 255, 0], [0, 0, 255], [0, 0, 255]],
            [[0, 255, 0], [0, 255, 0], [255, 0, 0], [255, 0, 0]],
            [[0, 255, 0], [0, 255, 0], [255, 0, 0], [255, 0, 0]],
        ],
        dtype=np.uint8,
    )
    clip = static_clip(QUADRANT)
    out180 = clip.with_effects([vfx.KeyframeMotion(rotation=180.0)])
    assert np.array_equal(out180.get_frame(0.0), expected_180)
    out360 = clip.with_effects([vfx.KeyframeMotion(rotation=360.0)])
    assert np.array_equal(out360.get_frame(0.0), QUADRANT)


def test_animated_rotation_full_turn():
    from moviepy import KeyframeTrack, vfx

    track = KeyframeTrack([(0.0, 0.0), (4.0, 360.0)])
    out = static_clip(QUADRANT, duration=4.0).with_effects(
        [vfx.KeyframeMotion(rotation=track)]
    )
    assert np.array_equal(out.get_frame(0.0), QUADRANT)
    assert np.array_equal(out.get_frame(1.0), QUADRANT_ROT90)
    assert np.array_equal(out.get_frame(3.0), np.rot90(QUADRANT, 3))
    assert np.array_equal(out.get_frame(4.0), QUADRANT)


def test_scale_down_centers_on_black_canvas():
    from moviepy import ColorClip, vfx

    clip = ColorClip(size=(4, 4), color=(200, 30, 60), duration=1.0)
    out = clip.with_effects([vfx.KeyframeMotion(scale=0.5)])
    frame = out.get_frame(0.0)
    expected = np.zeros((4, 4, 3), dtype=np.uint8)
    expected[1:3, 1:3] = (200, 30, 60)
    assert np.array_equal(frame, expected)


def test_scale_up_solid_stays_solid():
    from moviepy import ColorClip, vfx

    clip = ColorClip(size=(4, 4), color=(200, 30, 60), duration=1.0)
    out = clip.with_effects([vfx.KeyframeMotion(scale=2.0)])
    frame = out.get_frame(0.0)
    assert frame.shape == (4, 4, 3)
    assert np.all(frame == (200, 30, 60))


def test_position_static_center():
    from moviepy import vfx

    small = np.full((2, 2, 3), 255, dtype=np.uint8)
    clip = static_clip(small)
    out = clip.with_effects(
        [vfx.KeyframeMotion(position=(4, 3), canvas_size=(8, 6))]
    )
    expected = np.zeros((6, 8, 3), dtype=np.uint8)
    expected[2:4, 3:5] = 255
    assert np.array_equal(out.get_frame(0.0), expected)


def test_position_default_is_canvas_center():
    from moviepy import vfx

    small = np.full((2, 2, 3), 255, dtype=np.uint8)
    clip = static_clip(small)
    out = clip.with_effects([vfx.KeyframeMotion(canvas_size=(6, 4))])
    expected = np.zeros((4, 6, 3), dtype=np.uint8)
    expected[1:3, 2:4] = 255
    assert np.array_equal(out.get_frame(0.0), expected)


def test_animated_position_linear_motion():
    from moviepy import KeyframeTrack, vfx

    small = np.full((2, 2, 3), 255, dtype=np.uint8)
    clip = static_clip(small, duration=2.0)
    track = KeyframeTrack([(0.0, (2.0, 2.0)), (2.0, (6.0, 2.0))])
    out = clip.with_effects(
        [vfx.KeyframeMotion(position=track, canvas_size=(8, 4))]
    )
    expected0 = np.zeros((4, 8, 3), dtype=np.uint8)
    expected0[1:3, 1:3] = 255
    assert np.array_equal(out.get_frame(0.0), expected0)
    expected1 = np.zeros((4, 8, 3), dtype=np.uint8)
    expected1[1:3, 3:5] = 255
    assert np.array_equal(out.get_frame(1.0), expected1)


def test_position_clipped_at_canvas_edge():
    from moviepy import vfx

    small = np.full((2, 2, 3), 255, dtype=np.uint8)
    clip = static_clip(small)
    out = clip.with_effects(
        [vfx.KeyframeMotion(position=(0, 0), canvas_size=(4, 4))]
    )
    expected = np.zeros((4, 4, 3), dtype=np.uint8)
    expected[0, 0] = 255
    assert np.array_equal(out.get_frame(0.0), expected)


def test_transform_order_scale_then_rotation():
    from moviepy import vfx

    frame = np.zeros((2, 4, 3), dtype=np.uint8)
    frame[:] = (255, 0, 0)
    clip = static_clip(frame)
    out = clip.with_effects(
        [vfx.KeyframeMotion(scale=(2.0, 1.0), rotation=90.0)]
    )
    expected = np.zeros((2, 4, 3), dtype=np.uint8)
    expected[:, 1:3] = (255, 0, 0)
    assert np.array_equal(out.get_frame(0.0), expected)


def test_combined_motion_end_state():
    from moviepy import KeyframeTrack, vfx

    clip = static_clip(QUADRANT, duration=2.0)
    effect = vfx.KeyframeMotion(
        position=KeyframeTrack([(0.0, (2.0, 2.0)), (2.0, (4.0, 2.0))]),
        rotation=KeyframeTrack([(0.0, 0.0), (2.0, 90.0)]),
        canvas_size=(6, 4),
    )
    out = clip.with_effects([effect])
    expected = np.zeros((4, 6, 3), dtype=np.uint8)
    expected[0:4, 2:6] = QUADRANT_ROT90
    assert np.array_equal(out.get_frame(2.0), expected)


def test_mask_clips_rejected():
    from moviepy import VideoClip, vfx

    mask = VideoClip(
        frame_function=lambda t: np.ones((4, 4)), is_mask=True, duration=1.0
    )
    with pytest.raises(ValueError):
        mask.with_effects([vfx.KeyframeMotion()])


def test_effect_parameter_validation():
    from moviepy import KeyframeTrack, vfx

    clip = static_clip(QUADRANT)
    with pytest.raises(ValueError):
        clip.with_effects([vfx.KeyframeMotion(opacity=1.5)])
    with pytest.raises(ValueError):
        clip.with_effects(
            [vfx.KeyframeMotion(opacity=KeyframeTrack([(0.0, 0.5), (1.0, 2.0)]))]
        )
    with pytest.raises(ValueError):
        clip.with_effects([vfx.KeyframeMotion(scale=0.0)])
    with pytest.raises(ValueError):
        clip.with_effects([vfx.KeyframeMotion(scale=-1.0)])
    with pytest.raises(ValueError):
        clip.with_effects([vfx.KeyframeMotion(canvas_size=(0, 4))])
    with pytest.raises((TypeError, ValueError)):
        clip.with_effects(
            [vfx.KeyframeMotion(position=KeyframeTrack([(0.0, 1.0), (1.0, 2.0)]))]
        )
    with pytest.raises(TypeError):
        clip.with_effects([vfx.KeyframeMotion(position=5.0)])
    with pytest.raises(TypeError):
        clip.with_effects([vfx.KeyframeMotion(opacity="opaque")])
    with pytest.raises(TypeError):
        clip.with_effects([vfx.KeyframeMotion(canvas_size="big")])


# ------------------------------------------------------------------ Effect
# helper surface shared with the classical effects


def test_effect_base_class_exposes_time_varying_helper():
    """The shared resolver lives on ``moviepy.Effect``.

    Both the new ``KeyframeMotion`` and the classical effects use it to
    accept ``KeyframeTrack`` arguments in a consistent way. Exposing it on
    the base class lets user-defined effects opt in too.
    """
    from moviepy import Effect

    assert callable(getattr(Effect, "_resolve_time_varying", None))
    resolver = Effect._resolve_time_varying
    # numeric passthrough
    fn = resolver(1.5, name="test", expected="scalar")
    assert fn(0.0) == 1.5 and fn(9.0) == 1.5
    # None passthrough
    assert resolver(None, name="test", expected="scalar") is None


# ------------------------------------------------ Resize + KeyframeTrack


def test_resize_accepts_scalar_track():
    """A scalar KeyframeTrack is a time-varying uniform scaling factor."""
    from moviepy import ColorClip, KeyframeTrack, vfx

    clip = ColorClip(size=(4, 4), color=(200, 30, 60), duration=2.0)
    track = KeyframeTrack([(0.0, 0.5), (2.0, 1.0)])
    out = clip.with_effects([vfx.Resize(track)])
    # scaling: t=0 -> 0.5 -> 2x2; t=1 -> 0.75 -> 3x3; t=2 -> 1.0 -> 4x4
    assert out.get_frame(0.0).shape == (2, 2, 3)
    assert out.get_frame(1.0).shape == (3, 3, 3)
    assert out.get_frame(2.0).shape == (4, 4, 3)


def test_resize_accepts_tuple_track():
    """A 2-tuple KeyframeTrack is a time-varying (w, h)."""
    from moviepy import ColorClip, KeyframeTrack, vfx

    clip = ColorClip(size=(4, 4), color=(0, 128, 255), duration=2.0)
    track = KeyframeTrack([(0.0, (2.0, 2.0)), (2.0, (6.0, 6.0))])
    out = clip.with_effects([vfx.Resize(track)])
    assert out.get_frame(0.0).shape == (2, 2, 3)
    assert out.get_frame(1.0).shape == (4, 4, 3)
    assert out.get_frame(2.0).shape == (6, 6, 3)


def test_resize_static_backward_compat():
    """A static float still uses the cheap image_transform path."""
    from moviepy import ColorClip, vfx

    clip = ColorClip(size=(4, 4), color=(200, 30, 60), duration=1.0)
    out = clip.with_effects([vfx.Resize(0.5)])
    assert out.get_frame(0.0).shape == (2, 2, 3)


def test_resize_lambda_backward_compat():
    """A ``lambda t: factor`` still works as before."""
    from moviepy import ColorClip, vfx

    clip = ColorClip(size=(4, 4), color=(200, 30, 60), duration=1.0)
    out = clip.with_effects([vfx.Resize(lambda t: 0.5 + 0.25 * t)])
    assert out.get_frame(0.0).shape == (2, 2, 3)
    assert out.get_frame(1.0).shape == (3, 3, 3)


def test_resize_height_accepts_scalar_track():
    from moviepy import ColorClip, KeyframeTrack, vfx

    clip = ColorClip(size=(4, 4), color=(200, 30, 60), duration=1.0)
    height_track = KeyframeTrack([(0.0, 2.0), (1.0, 4.0)])
    out = clip.with_effects([vfx.Resize(height=height_track)])
    # height 2 -> width preserves aspect (2), height 4 -> width 4
    assert out.get_frame(0.0).shape == (2, 2, 3)
    assert out.get_frame(1.0).shape == (4, 4, 3)


def test_resize_rejects_tuple_track_for_height():
    from moviepy import ColorClip, KeyframeTrack, vfx

    clip = ColorClip(size=(4, 4), color=(200, 30, 60), duration=1.0)
    bad = KeyframeTrack([(0.0, (2.0, 4.0)), (1.0, (4.0, 4.0))])
    with pytest.raises((TypeError, ValueError)):
        clip.with_effects([vfx.Resize(height=bad)])
    with pytest.raises((TypeError, ValueError)):
        clip.with_effects([vfx.Resize(width=bad)])


# ------------------------------------------------ Rotate + KeyframeTrack


def test_rotate_accepts_scalar_track():
    from moviepy import KeyframeTrack, vfx

    clip = static_clip(QUADRANT, duration=2.0)
    track = KeyframeTrack([(0.0, 0.0), (2.0, 360.0)])
    out = clip.with_effects([vfx.Rotate(angle=track)])
    assert np.array_equal(out.get_frame(0.0), QUADRANT)
    assert np.array_equal(out.get_frame(0.5), QUADRANT_ROT90)
    assert np.array_equal(out.get_frame(1.0), np.rot90(QUADRANT, 2))
    assert np.array_equal(out.get_frame(1.5), np.rot90(QUADRANT, 3))
    assert np.array_equal(out.get_frame(2.0), QUADRANT)


def test_rotate_static_backward_compat():
    from moviepy import vfx

    out = static_clip(QUADRANT).with_effects([vfx.Rotate(angle=90.0)])
    assert np.array_equal(out.get_frame(0.0), QUADRANT_ROT90)


def test_rotate_lambda_backward_compat():
    from moviepy import vfx

    out = static_clip(QUADRANT, duration=4.0).with_effects(
        [vfx.Rotate(angle=lambda t: 90.0 * t, expand=False)]
    )
    assert np.array_equal(out.get_frame(0.0), QUADRANT)
    assert np.array_equal(out.get_frame(1.0), QUADRANT_ROT90)


def test_rotate_rejects_tuple_track():
    from moviepy import KeyframeTrack, vfx

    clip = static_clip(QUADRANT)
    bad = KeyframeTrack([(0.0, (0.0, 0.0)), (1.0, (90.0, 45.0))])
    with pytest.raises((TypeError, ValueError)):
        # The tuple-track rejection must fire at effect-application time,
        # not deep inside frame rendering; sample a frame to force apply.
        out = clip.with_effects([vfx.Rotate(angle=bad)])
        out.get_frame(0.0)


# --------------------------------------------- MultiplyColor + KeyframeTrack


def test_multiplycolor_accepts_scalar_track():
    from moviepy import ColorClip, KeyframeTrack, vfx

    clip = ColorClip(size=(1, 1), color=(200, 100, 48), duration=2.0)
    track = KeyframeTrack([(0.0, 1.0), (2.0, 0.25)])
    out = clip.with_effects([vfx.MultiplyColor(track)])
    assert list(out.get_frame(0.0)[0, 0]) == [200, 100, 48]
    # linear interpolation: t=1.0 -> factor 0.625 -> (125, 62, 30)
    assert list(out.get_frame(1.0)[0, 0]) == [125, 62, 30]
    assert list(out.get_frame(2.0)[0, 0]) == [50, 25, 12]


def test_multiplycolor_static_backward_compat():
    from moviepy import ColorClip, vfx

    clip = ColorClip(size=(1, 1), color=(200, 100, 48), duration=1.0)
    out = clip.with_effects([vfx.MultiplyColor(0.5)])
    assert list(out.get_frame(0.0)[0, 0]) == [100, 50, 24]


def test_multiplycolor_rejects_negative_factor():
    from moviepy import ColorClip, KeyframeTrack, vfx

    clip = ColorClip(size=(1, 1), color=(200, 100, 48), duration=1.0)
    with pytest.raises(ValueError):
        clip.with_effects([vfx.MultiplyColor(-0.5)])
    with pytest.raises(ValueError):
        clip.with_effects(
            [vfx.MultiplyColor(KeyframeTrack([(0.0, 1.0), (1.0, -0.5)]))]
        )


def test_multiplycolor_rejects_tuple_track():
    from moviepy import ColorClip, KeyframeTrack, vfx

    clip = ColorClip(size=(1, 1), color=(200, 100, 48), duration=1.0)
    bad = KeyframeTrack([(0.0, (0.5, 0.5)), (1.0, (1.0, 1.0))])
    with pytest.raises((TypeError, ValueError)):
        clip.with_effects([vfx.MultiplyColor(bad)])


# --------------------------------------------- with_opacity + KeyframeTrack


def test_with_opacity_accepts_scalar_track():
    """with_opacity supports an animated opacity via a scalar KeyframeTrack.

    The clip is composited over black so the observed pixel is the
    faithful ``color * opacity`` product per frame.
    """
    from moviepy import ColorClip, KeyframeTrack, CompositeVideoClip

    bg = ColorClip(size=(1, 1), color=(0, 0, 0), duration=2.0)
    fg = ColorClip(size=(1, 1), color=(200, 100, 48), duration=2.0)
    track = KeyframeTrack([(0.0, 1.0), (2.0, 0.5)])
    composite = CompositeVideoClip([bg, fg.with_opacity(track)])
    assert list(composite.get_frame(0.0)[0, 0]) == [200, 100, 48]
    # t=0.5 -> opacity 0.875 -> (175, 87.5, 42) -> uint8 (175, 87, 42)
    assert list(composite.get_frame(0.5)[0, 0]) == [175, 87, 42]
    assert list(composite.get_frame(1.0)[0, 0]) == [150, 75, 36]
    assert list(composite.get_frame(1.5)[0, 0]) == [125, 62, 30]


def test_with_opacity_static_backward_compat():
    from moviepy import ColorClip, CompositeVideoClip

    bg = ColorClip(size=(1, 1), color=(0, 0, 0), duration=1.0)
    fg = ColorClip(size=(1, 1), color=(200, 100, 48), duration=1.0).with_opacity(0.5)
    composite = CompositeVideoClip([bg, fg])
    assert list(composite.get_frame(0.5)[0, 0]) == [100, 50, 24]


def test_with_opacity_callable_backward_compat():
    from moviepy import ColorClip, CompositeVideoClip

    bg = ColorClip(size=(1, 1), color=(0, 0, 0), duration=2.0)
    fg = ColorClip(
        size=(1, 1), color=(200, 100, 48), duration=2.0
    ).with_opacity(lambda t: 1.0 - 0.25 * t)
    composite = CompositeVideoClip([bg, fg])
    assert list(composite.get_frame(0.0)[0, 0]) == [200, 100, 48]
    assert list(composite.get_frame(1.0)[0, 0]) == [150, 75, 36]


def test_with_opacity_rejects_tuple_track():
    from moviepy import ColorClip, KeyframeTrack

    fg = ColorClip(size=(1, 1), color=(200, 100, 48), duration=1.0)
    bad = KeyframeTrack([(0.0, (1.0, 1.0)), (1.0, (0.5, 0.5))])
    with pytest.raises((TypeError, ValueError)):
        fg.with_opacity(bad)


# --------------------------------------------- with_position + KeyframeTrack


def test_with_position_accepts_track():
    from moviepy import ColorClip, KeyframeTrack, CompositeVideoClip

    bg = ColorClip(size=(6, 4), color=(0, 0, 0), duration=2.0)
    small = ColorClip(size=(2, 2), color=(255, 255, 255), duration=2.0)
    pos = KeyframeTrack([(0.0, (0.0, 0.0)), (2.0, (4.0, 2.0))])
    composite = CompositeVideoClip([bg, small.with_position(pos)])
    # t=0 -> top-left (0, 0), 2x2 block occupies rows/cols 0..1
    frame0 = composite.get_frame(0.0)
    white = np.argwhere(frame0[:, :, 0] == 255).tolist()
    assert white == [[0, 0], [0, 1], [1, 0], [1, 1]]
    # t=1 -> (2, 1), block occupies rows 1..2, cols 2..3
    frame1 = composite.get_frame(1.0)
    white1 = np.argwhere(frame1[:, :, 0] == 255).tolist()
    assert white1 == [[1, 2], [1, 3], [2, 2], [2, 3]]


def test_with_position_pair_backward_compat():
    from moviepy import ColorClip, CompositeVideoClip

    bg = ColorClip(size=(6, 4), color=(0, 0, 0), duration=1.0)
    small = ColorClip(size=(2, 2), color=(255, 255, 255), duration=1.0)
    composite = CompositeVideoClip([bg, small.with_position((2, 1))])
    frame = composite.get_frame(0.0)
    white = np.argwhere(frame[:, :, 0] == 255).tolist()
    assert white == [[1, 2], [1, 3], [2, 2], [2, 3]]


def test_with_position_lambda_backward_compat():
    from moviepy import ColorClip, CompositeVideoClip

    bg = ColorClip(size=(6, 4), color=(0, 0, 0), duration=2.0)
    small = ColorClip(size=(2, 2), color=(255, 255, 255), duration=2.0)
    composite = CompositeVideoClip(
        [bg, small.with_position(lambda t: (int(2 * t), 1))]
    )
    frame = composite.get_frame(1.0)
    white = np.argwhere(frame[:, :, 0] == 255).tolist()
    assert white == [[1, 2], [1, 3], [2, 2], [2, 3]]


def test_with_position_rejects_scalar_track():
    from moviepy import ColorClip, KeyframeTrack

    small = ColorClip(size=(2, 2), color=(255, 255, 255), duration=1.0)
    bad = KeyframeTrack([(0.0, 0.0), (1.0, 4.0)])
    with pytest.raises((TypeError, ValueError)):
        small.with_position(bad)


# ------------------------------------------------- chained-effect integration


def test_track_and_effects_do_not_leak_between_applications():
    """The same effect + track instance is safe to apply to two clips."""
    from moviepy import ColorClip, KeyframeTrack, vfx

    track = KeyframeTrack([(0.0, 1.0), (2.0, 0.5)])
    effect = vfx.MultiplyColor(track)

    clip_a = ColorClip(size=(1, 1), color=(200, 100, 48), duration=2.0)
    clip_b = ColorClip(size=(1, 1), color=(100, 50, 24), duration=2.0)
    out_a = clip_a.with_effects([effect])
    out_b = clip_b.with_effects([effect])
    assert list(out_a.get_frame(0.0)[0, 0]) == [200, 100, 48]
    assert list(out_b.get_frame(0.0)[0, 0]) == [100, 50, 24]
    assert list(out_a.get_frame(2.0)[0, 0]) == [100, 50, 24]
    assert list(out_b.get_frame(2.0)[0, 0]) == [50, 25, 12]


def test_track_composes_with_keyframe_motion():
    """A track-driven Resize can be chained with a KeyframeMotion."""
    from moviepy import ColorClip, KeyframeTrack, vfx

    clip = ColorClip(size=(4, 4), color=(200, 100, 48), duration=1.0)
    resize_track = KeyframeTrack([(0.0, 0.5), (1.0, 0.5)])
    # Resize -> KeyframeMotion(opacity=0.5) -> pixel is (100, 50, 24)
    out = clip.with_effects(
        [vfx.Resize(resize_track), vfx.KeyframeMotion(opacity=0.5)]
    )
    frame = out.get_frame(0.0)
    # After 0.5x resize the clip is 2x2 centered on the 2x2 canvas,
    # so every pixel is (100, 50, 24) (opacity 0.5 halved from 200,100,48).
    assert frame.shape == (2, 2, 3)
    assert list(frame[0, 0]) == [100, 50, 24]
    assert list(frame[1, 1]) == [100, 50, 24]
