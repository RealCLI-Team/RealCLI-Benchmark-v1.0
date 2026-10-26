# Help me turn moviepy into a tool that "describes animation with keyframes"

I make motion-graphics-style short videos using moviepy in `/app` (the source is right there, and `import moviepy` works directly). Right now, for a shot that "scales up from small, rotates while moving to some position, and fades in and out at the same time", I would write something like this:

```python
from moviepy import VideoFileClip, CompositeVideoClip

clip = VideoFileClip("logo.mp4")

zoom = clip.with_effects([
    # 从 0.5 缩放到 1.5，两秒内完成
    __import__("moviepy").vfx.Resize(lambda t: 0.5 + (1.5 - 0.5) * (t / 2.0)),
    # 顺便旋转 90 度
    __import__("moviepy").vfx.Rotate(angle=lambda t: 90.0 * (t / 2.0), expand=False),
]).with_position(lambda t: (100 + int(200 * t), 100 + int(100 * t)))
```

Problems galore:

- Every segment of animation needs a freshly made `lambda t: ...`, and easing is all hand-computed — to do "slow first, then fast, with a little bounce at the end" I'd have to write polynomials myself, and it quickly becomes unmaintainable.
- Reusing the same animation on another clip, playing it in reverse, or delaying its start by 0.5 seconds all require writing yet another lambda.
- Different parameters (scale, rotation, position, opacity) have inconsistent semantics: `Resize` takes a `lambda`, `with_position` takes a `lambda`, yet parameter validation, boundary clamping, and out-of-timeline extrapolation all differ from one to another. I want a unified data structure that "describes how a property varies over time".

## What I want you to do

Please add a **keyframe animation system** to moviepy and use it as the core to unify how animations are defined:

1. I can describe how any property varies over time with a series of "(time, value)" pairs plus an easing name, and get a **keyframe track** object. A track can be scalar (opacity, rotation angle, uniform scale) or an equal-length vector ((x, y) position, (sx, sy) non-uniform scale); when querying the value at any time (`value_at(t)`) it should interpolate between the two adjacent keyframes according to the easing, and outside the timeline it automatically clamps to the first/last values; the track's keyframe times and values must be readable via the `times` / `values` attributes (tuples). The track object must support the derived transformations "scale all values, shift all times, play in reverse", with method names `rescaled(factor)` / `shifted(offset)` / `reversed()` respectively (`shifted` must not allow shifting times to negative values); the derived result must be a new track that does not affect the original. I also want two convenient factories, a constant track and a single-segment ramp track: `KeyframeTrack.constant(value)` (a single keyframe at time 0) and `KeyframeTrack.ramp(start, end, duration, easing=...)` (two keyframes from start to end spanning duration; duration must be positive, otherwise rejected). When the constructor meets unreasonable input (empty sequence, non-increasing times, inconsistent value types, illegal easing name, etc.), it must reject it directly with `TypeError`/`ValueError`, not wait until value query time to blow up. Besides `times` / `values`, a track also exposes the `duration` (last keyframe time minus the first), `is_tuple` (whether the values are equal-length vectors), and `easing` (the resolved easing callable) attributes, and supports `len()` (the number of keyframes). `reversed()` re-anchors the timeline to `[0, duration]`: each keyframe originally at time `τ` is relocated to `duration - τ` with its value unchanged (so the keyframes come out in ascending time order with the value sequence flipped; for a `linear` track this is equivalent to `r.value_at(t) == self.value_at(duration - t)`), and the easing keeps the original callable — it is **not** mirrored, it still applies to the segment progress in the forward direction.

2. A full set of **easing functions** must be implemented following Robert Penner's classic formulas (consistent with the reference implementations on easings.net), covering the in / out / in-out variants of quad / cubic / quart / quint / sine / expo / circ / back / elastic / bounce, plus `linear` and a `hold` that "jumps to the final value only at the very last moment". Easing functions must be retrievable by name, and must also allow me to pass in a custom callable. The registry is called `moviepy.easing.EASINGS` (a "name → function" dictionary), and the names in it are **exactly the following 32**, no more, no fewer:

   ```
   linear, hold,
   ease_in_quad, ease_out_quad, ease_in_out_quad,
   ease_in_cubic, ease_out_cubic, ease_in_out_cubic,
   ease_in_quart, ease_out_quart, ease_in_out_quart,
   ease_in_quint, ease_out_quint, ease_in_out_quint,
   ease_in_sine, ease_out_sine, ease_in_out_sine,
   ease_in_expo, ease_out_expo, ease_in_out_expo,
   ease_in_circ, ease_out_circ, ease_in_out_circ,
   ease_in_back, ease_out_back, ease_in_out_back,
   ease_in_elastic, ease_out_elastic, ease_in_out_elastic,
   ease_in_bounce, ease_out_bounce, ease_in_out_bounce
   ```

   These 32 functions must also be importable directly from the `moviepy.easing` module by name (e.g. `from moviepy.easing import ease_in_quad, hold`).

3. **A batch of existing vfx / clip methods must be able to consume this track directly** — this is the key part:

   - The `new_size` / `height` / `width` parameters of `vfx.Resize`: previously accepted a number, a `(w, h)` tuple, or a `lambda t: ...`; they should now also accept a keyframe track — a scalar track is treated as a uniform scale factor, and a `(w, h)` 2-D track as per-frame dimensions. When passed to `height` / `width` individually, only scalar tracks are allowed.
   - The `angle` of `vfx.Rotate`: previously accepted a number or a `lambda t: ...`; it now also accepts a scalar keyframe track. Using a 2-D track as an angle is meaningless and must be rejected.
   - The `factor` of `vfx.MultiplyColor`: previously accepted a single number; please extend it to also support `lambda t: ...` and scalar tracks (after all, effects like "fading to almost full black" are its job). The factor must be non-negative; negative values — whether given statically or as a keyframe value inside a track — must be rejected immediately. At the same time, the static-number usage of the existing API must remain **bit-for-bit unchanged** (same clip, same number, identical output pixels).
   - `VideoClip.with_position`: previously accepted `(x, y)`, string pairs like `("center", "top")`, or `lambda t: (x, y)`; it now also accepts an `(x, y)` keyframe track.
   - `VideoClip.with_opacity`: previously accepted a static float; please extend it to also support `lambda t: opacity` and scalar keyframe tracks, achieving genuinely time-varying opacity. Given a static number, the old behavior must remain unchanged.

   Note: for all existing interfaces, the behavior with the **original** input kinds (number / `lambda t:` / static tuple) must remain pixel-for-pixel identical — adding track support must not quietly change the old paths. Track type mismatches (e.g. stuffing a 2-D track into `angle`, or a scalar track into `with_position`) must raise `TypeError` or `ValueError` **at the moment the effect is applied**, not blow up later when some frame is rendered.

   The normalization of the three input kinds — "static value / `lambda t:` / keyframe track" — for the batch of effects above (including the new `KeyframeMotion`) must go through one shared implementation: make it `_resolve_time_varying(value, name=..., expected=...)` on the `moviepy.Effect` base class (`name` is the parameter name, used in error messages; `expected` declares the expected shape, e.g. `"scalar"`), so that user-defined effects can reuse it directly. Its contract: a static number is normalized into a constant function that "returns that value at any time"; `None` is returned as-is as `None` (meaning the parameter is unset); a callable is used as-is as a `t -> 值` function; a keyframe track is normalized into its value-query function; when the type or shape doesn't match, it raises `TypeError`/`ValueError` at the moment `_resolve_time_varying` is called.

4. On top of that, give me a **new composite effect** `vfx.KeyframeMotion`, which in one step performs "scale → rotate → translate → blend onto a fixed-size black canvas → apply overall opacity", and outputs a new clip of constant size:

   - The parameters `position` `scale` `rotation` `opacity` `canvas_size` are all keyword-only and all optional; besides static numbers/tuples, `position` `scale` `rotation` `opacity` also accept keyframe tracks of the corresponding shapes (when `scale` is given a scalar, it expands isotropically to `(s, s)`).
   - The transform order is fixed: scale first, then rotate, then align the transformed center to `position`; opacity acts last on the whole canvas's pixels (equivalent to blending with a black background), final pixel = `round(画布像素 × opacity)`.
   - Scaling resizes the source frame to `(round(w·sx), round(h·sy))` (never below 1×1); rotation keeps the size unchanged and crops the excess; both scaling and rotation use **bilinear resampling**.
   - `canvas_size` defaults to the same size as the source clip; the output clip's duration, fps, audio, and mask all match the source clip; the source clip itself must not be modified.
   - A mask clip with `is_mask=True` used as input is meaningless and must raise `ValueError`. The same effect instance may be applied repeatedly to different clips without mutual interference.
   - All kinds of parameter errors (`opacity` out of range, non-positive `scale`, non-positive `canvas_size`, `position`/`scale` given a track of the wrong shape, etc.) must raise `TypeError`/`ValueError` at the moment the effect is applied.

5. The user-facing API:

   ```python
   from moviepy import KeyframeTrack, vfx, easing  # 都能从顶层导入
   ```

   `moviepy.easing` is an importable submodule containing the `EASINGS` registry described above, plus a general resolution function `get_easing`: passing a string looks it up in the table (an unregistered name raises `ValueError`), passing a callable returns it as-is, and passing any other type is rejected with `TypeError`.

## How I'll use it

Once it's done, I'll use it like this:

```python
from moviepy import KeyframeTrack, vfx

motion = vfx.KeyframeMotion(
    position=KeyframeTrack(
        [(0, (100, 100)), (2, (500, 300))],
        easing="ease_in_out_cubic",
    ),
    scale=KeyframeTrack.ramp(0.5, 1.5, 2.0),
    rotation=KeyframeTrack.ramp(0.0, 90.0, 2.0),
    opacity=KeyframeTrack([(0, 0.0), (1, 1.0)], easing="ease_out_quad"),
)
animated = clip.with_effects([motion])
```

And mixed directly into existing effects:

```python
fade_in = KeyframeTrack.ramp(0.0, 1.0, 1.0, easing="ease_out_quad")
zoom = KeyframeTrack([(0.0, 0.5), (2.0, 1.5)], easing="ease_in_out_cubic")

clip.with_effects([vfx.Resize(zoom)]).with_opacity(fade_in)
```

## Other preferences

- Do **frame computation only**, and introduce no new third-party dependencies; rendering only needs numpy / PIL level operations.
- Keep the code style, docstring style, and directory organization consistent with moviepy's existing conventions (one file per effect, `dataclass`, applying via `clip.with_effects([...])`, etc.).
- Following the repository's existing conventions, add **automated regression tests** covering the behavior of the new capabilities, and while you're at it also cover how the existing interfaces behave with the new-shaped parameters — don't break the existing static number / `lambda t:` usages.
