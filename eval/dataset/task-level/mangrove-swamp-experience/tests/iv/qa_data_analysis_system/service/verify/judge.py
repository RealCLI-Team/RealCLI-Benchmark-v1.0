#!/usr/bin/env python3
"""Judge a single checklist item against execution evidence.

Each item names a ``mode`` that selects which evidence the judge reasons over:

  * **LLM Judge** — DOM / source code text only.
  * **VLM Judge** — rendered screenshots (sent to the model as real images).
  * **LLM-Based Browser Agent Judge** — the interaction trace.
  * **VLM-Based Browser Agent Judge** — the interaction trace + screenshots.

The model itself is INJECTED (``model_call``) so this module unit-tests without
a network. For the two VLM modes we build OpenAI-style multimodal messages with
base64 image blocks, so the judge actually *sees* the rendered pages. A model
failure or a non-boolean answer yields ``satisfied=None`` (indeterminate) —
never a silent "satisfied", which would inflate the score.
"""

from __future__ import annotations

import base64
import os
from typing import Any, Callable

MODE_EVIDENCE = {
    "LLM Judge": "dom/code",
    "VLM Judge": "frames",
    "LLM-Based Browser Agent Judge": "trace",
    "VLM-Based Browser Agent Judge": "trace+frames",
}

# cap images per judge call to bound tokens/latency
MAX_FRAMES = 4

_SYS = ("You are a strict frontend-artifact verifier. Given a single binary check "
        "and execution evidence, decide if the artifact SATISFIES it. Be concrete and "
        "cite what you actually see. Respond with a JSON object: "
        '{"satisfied": true|false, "rationale": "one or two sentences citing evidence"}.')


def _select_frames(frames: list[str]) -> list[str]:
    """Pick a small, representative set: the load/stable shots plus a couple of
    interaction shots. Falls back to the first MAX_FRAMES if names are unknown."""
    if not frames:
        return []
    prefer = ("frame_stable", "frame_first_paint", "frame_responsive_mobile")
    picked: list[str] = []
    for key in prefer:
        for f in frames:
            if key in os.path.basename(f) and f not in picked:
                picked.append(f)
                break
    for f in frames:
        if len(picked) >= MAX_FRAMES:
            break
        if f not in picked:
            picked.append(f)
    return picked[:MAX_FRAMES]


def _image_block(path: str) -> dict[str, Any] | None:
    try:
        data = base64.b64encode(open(path, "rb").read()).decode("ascii")
    except OSError:
        return None
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}}


def _build_messages(item: dict[str, Any], evidence: dict[str, Any]) -> tuple[list[dict], list[str]]:
    """Return (messages, used_frames). Uses multimodal content when the mode
    calls for frames and images are available."""
    ev = MODE_EVIDENCE.get(item["mode"], "dom/code")
    text_parts = [f"Check: {item['checking_content']}",
                  f"Expected result: {item['expected_result']}"]
    if "dom" in ev or "code" in ev:
        body = evidence.get("dom") or (evidence.get("code", "") or "")[:4000]
        text_parts.append(f"DOM/code:\n{body}")
    if "trace" in ev:
        text_parts.append(f"Interaction trace:\n{evidence.get('trace')}")
        errs = evidence.get("console_errors") or []
        if errs:
            text_parts.append(f"Console errors:\n{errs[:10]}")

    used_frames: list[str] = []
    image_blocks: list[dict] = []
    if "frames" in ev:
        used_frames = _select_frames(evidence.get("frames") or [])
        for f in used_frames:
            blk = _image_block(f)
            if blk is not None:
                image_blocks.append(blk)
        if image_blocks:
            text_parts.append(f"Screenshots ({len(image_blocks)}) are attached below.")
        else:
            text_parts.append("(No screenshots were captured for this artifact.)")

    text = "\n\n".join(text_parts)
    if image_blocks:
        user_content: Any = [{"type": "text", "text": text}, *image_blocks]
    else:
        user_content = text
    messages = [{"role": "system", "content": _SYS},
                {"role": "user", "content": user_content}]
    return messages, [os.path.basename(f) for f in used_frames]


def _prompt_text(messages: list[dict], used_frames: list[str]) -> str:
    """Render the messages sent to the judge into a readable prompt string, so the
    UI can show exactly how each judge was prompted. Image blocks are collapsed to
    a placeholder (the actual screenshots are shown separately as thumbnails)."""
    lines: list[str] = []
    for m in messages:
        role = m.get("role", "?").upper()
        content = m.get("content")
        if isinstance(content, list):
            parts = []
            for blk in content:
                if blk.get("type") == "text":
                    parts.append(blk.get("text", ""))
                elif blk.get("type") == "image_url":
                    parts.append("[image attached]")
            body = "\n".join(parts)
        else:
            body = str(content)
        lines.append(f"### {role}\n{body}")
    if used_frames:
        lines.append(f"[{len(used_frames)} screenshot(s) sent: {', '.join(used_frames)}]")
    return "\n\n".join(lines)


def judge_item(item: dict[str, Any], evidence: dict[str, Any],
               model_call: Callable[[list[dict[str, str]]], dict]) -> dict[str, Any]:
    mode_ref = MODE_EVIDENCE.get(item["mode"], "dom/code")
    messages, used_frames = _build_messages(item, evidence)
    prompt = _prompt_text(messages, used_frames)
    try:
        obj = model_call(messages)
        sat = obj.get("satisfied")
        satisfied = bool(sat) if isinstance(sat, bool) else None
        rationale = str(obj.get("rationale", ""))[:800]
    except Exception as exc:  # noqa: BLE001 - any model failure => indeterminate
        return {"satisfied": None, "rationale": f"judge error: {exc}"[:200],
                "evidence_ref": mode_ref, "frames_used": [], "prompt": prompt}
    return {"satisfied": satisfied, "rationale": rationale,
            "evidence_ref": mode_ref, "frames_used": used_frames, "prompt": prompt}
