#!/usr/bin/env python3
"""Shared image-block helper for the model-call stages.

`planner.py` (initial screenshot) and `scorer.py` (downsampled trajectory
frames) both attach screenshots to model messages as OpenAI `image_url`
content blocks. Keeping the encoding here means one implementation and one
place to change the encoding/format.

Frames are captured as 1280x720 PNGs (~210 KB each). Uploading them verbatim
is what made judging slow, and the cost is NOT linear — it is a cliff.
Measured 2026-08-14 against the live judge gateway, 12 two-frame calls at
6-way concurrency:

    payload/call   median     p90      errors
    1992 KB (PNG)   53.4 s   400.0 s    3/12   <- what we were sending
     217 KB (JPEG)  13.9 s    26.3 s    0/12
     146 KB (JPEG)   5.5 s    11.1 s    0/12
      43 KB (JPEG)   3.6 s     5.0 s    0/12

This reframes an earlier misdiagnosis worth recording, because it cost hours:
the ~265 s stalls looked like a BIMODAL GATEWAY DEFECT (median 1.8 s, p90
265 s, nothing in between) and were "fixed" by raising --judge-timeout above
the plateau. That was treating the symptom. Text-only calls to the same
gateway at the same time return in 1.5 s median / 4.6 s max, so the gateway is
healthy — the stall was our own 2 MB uploads, and the timeout raise only
stopped us from abandoning them after paying for them.

Encoding at JPEG q85 / max width 1024 keeps UI text legible (the judge must
read labels, buttons and score readouts to decide an item) while cutting the
payload ~14x. Resolution is deliberately NOT pushed lower: at 960px the
measurement got worse, not better, which is noise from competing with the
live sweep rather than a real gain, and shrinking further trades judging
accuracy — this output selects training data — for speed we no longer need.

Set IV_IMAGE_MAX_W=0 to send the original PNG (bisecting a judging
regression); IV_IMAGE_QUALITY tunes the JPEG quality.
"""
from __future__ import annotations

import base64
import io
import os
import threading
from typing import Any

_MAX_W = int(os.environ.get("IV_IMAGE_MAX_W", "1024"))
_QUALITY = int(os.environ.get("IV_IMAGE_QUALITY", "85"))

# Every checklist item in a task judges against the SAME shared frame set (see
# scorer.score: frames are selected once and passed to each item), so without a
# cache a 12-item task re-decodes and re-JPEGs the same ~6 frames up to 12
# times. That was invisible while judging was network-bound, but the payload fix
# moved the pipeline to CPU-bound — the box now runs at 98% of its 40-core quota
# with Chrome alone taking ~31 cores — so duplicate encoding is directly
# throughput we lose.
#
# Keyed by (path, mtime, size, encoder settings) so a rewritten frame is never
# served stale. Bounded because a sweep touches ~5.8k frames and the entries
# hold encoded bytes; the working set is one task's frames, so a small cap hits
# nearly always. Locked because scorer fans items across a thread pool.
_CACHE_MAX = int(os.environ.get("IV_IMAGE_CACHE", "64"))
_cache: dict[tuple, tuple[bytes, str]] = {}
_cache_lock = threading.Lock()


def _encode(raw: bytes) -> tuple[bytes, str]:
    """Downscale+JPEG-encode, falling back to the original bytes.

    Pillow is not a hard dependency of this pipeline and a frame can be
    truncated on disk, so any failure returns the original PNG rather than
    dropping the frame: a slow judge call beats a judge call with no evidence.
    """
    if _MAX_W <= 0:
        return raw, "png"
    try:
        from PIL import Image
    except ImportError:
        return raw, "png"
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
        im = im.convert("RGB")
        w, h = im.size
        if w > _MAX_W:
            im = im.resize((_MAX_W, max(1, round(h * _MAX_W / w))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=_QUALITY, optimize=True)
        return buf.getvalue(), "jpeg"
    except Exception:  # noqa: BLE001 - corrupt/partial frame: send it as-is
        return raw, "png"


def image_block(path: str) -> dict[str, Any] | None:
    """Encode a screenshot into an `image_url` content block.

    Returns None if the file cannot be read, so callers can skip a missing
    frame without special-casing the error.
    """
    key = None
    try:
        st = os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size, _MAX_W, _QUALITY)
    except OSError:
        return None
    with _cache_lock:
        hit = _cache.get(key)
    if hit is None:
        try:
            raw = open(path, "rb").read()
        except OSError:
            return None
        hit = _encode(raw)
        with _cache_lock:
            if len(_cache) >= _CACHE_MAX:
                # Plain FIFO eviction: the access pattern is one task's frames
                # judged together then never revisited, so recency tracking would
                # cost more than it saves.
                for k in list(_cache)[:max(1, _CACHE_MAX // 4)]:
                    _cache.pop(k, None)
            _cache[key] = hit
    data, mime = hit
    b64 = base64.b64encode(data).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{b64}"}}
