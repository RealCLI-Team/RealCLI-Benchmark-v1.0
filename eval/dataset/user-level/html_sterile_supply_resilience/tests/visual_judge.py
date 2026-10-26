#!/usr/bin/env python3
"""Strict OpenAI-compatible VLM judge for rendered-layout rubrics."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import time
import urllib.request
from pathlib import Path


def post_json(url: str, key: str, payload: dict, timeout: int = 600) -> dict:
    request = urllib.request.Request(
        url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rubrics", type=Path, required=True)
    parser.add_argument("--render-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--details", type=Path, required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    all_rubrics = json.loads(args.rubrics.read_text(encoding="utf-8"))["rubrics"]
    visual = [(idx, item) for idx, item in enumerate(all_rubrics) if item.get("evaluation_class") == "visual"]
    if len(visual) != 3:
        raise ValueError(f"expected exactly three visual rubrics, got {len(visual)}")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    content: list[dict] = [{
        "type": "text",
        "text": (
            "Evaluate the three visual-design criteria only from the rendered images. "
            "Judge hierarchy, legibility, analytical visual communication, layout, consistency, "
            "responsive behavior, clipping and professional polish. Do not infer unseen content.\n\n"
            + "\n".join(
                f"Rubric {idx}: {item['rubric']}\nCriterion: {item['criterion'][0]}"
                for idx, item in visual
            )
            + "\n\nReturn JSON only: {\"criteria_results\":[{\"rubric_index\":15,"
              "\"passed\":true,\"reasoning\":\"...\",\"evidence\":\"image names\"}, ...]}. "
              "Cover each listed rubric_index exactly once."
        ),
    }]
    for image in manifest.get("images", []):
        path = args.render_dir / image["path"]
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        content.append({"type": "text", "text": f"Rendered image: {path.name}"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}})
    api_base = os.environ["JUDGE_API_BASE"]
    api_key = os.environ["JUDGE_API_KEY"]
    payload = {
        "model": args.model,
        "temperature": 0,
        "max_tokens": 4096,
        "messages": [
            {"role": "system", "content": "You are a strict visual design evaluator. Return valid JSON only."},
            {"role": "user", "content": content},
        ],
    }
    expected = {idx for idx, _ in visual}
    last_error: Exception | None = None
    rows = None
    for attempt, delay in enumerate((0, 2, 5, 15)):
        if delay:
            time.sleep(delay)
        try:
            response = post_json(api_base, api_key, payload)
            message = response["choices"][0]["message"]["content"]
            parsed = parse_json(message)
            candidate = parsed.get("criteria_results")
            if (
                not isinstance(candidate, list)
                or len(candidate) != len(expected)
                or not all(isinstance(row, dict) for row in candidate)
                or {row.get("rubric_index") for row in candidate} != expected
                or not all(isinstance(row.get("passed"), bool) for row in candidate)
            ):
                raise ValueError(
                    f"VLM result did not exactly cover visual rubrics: {candidate}"
                )
            rows = candidate
            break
        except Exception as exc:  # retry transport, JSON and schema failures
            last_error = exc
            if attempt == 3:
                raise
    if rows is None:
        raise RuntimeError("VLM judge produced no result") from last_error
    results = []
    lookup = dict(visual)
    for row in sorted(rows, key=lambda item: item["rubric_index"]):
        idx = row["rubric_index"]
        if not isinstance(row.get("passed"), bool):
            raise ValueError(f"visual passed must be boolean: {row}")
        rubric = lookup[idx]
        results.append({
            "index": idx,
            "rubric": rubric["rubric"],
            "weight": rubric["weight"],
            "evaluation_class": "visual",
            "evaluation_mode": "rendered_vlm",
            "result": {
                "passed": row["passed"],
                "score": rubric["weight"] if row["passed"] else 0,
                "criteria_count": 1,
                "criteria_passed": int(row["passed"]),
                "criteria_results": [{
                    "index": 0,
                    "criterion": rubric["criterion"][0],
                    "passed": row["passed"],
                    "reasoning": str(row.get("reasoning", "")),
                    "evidence": str(row.get("evidence", "")),
                }],
                "overall_reasoning": str(row.get("reasoning", "")),
            },
        })
    args.details.write_text(json.dumps({
        "schema_version": "manual-hard6.visual-judge.v1",
        "model": args.model,
        "vision_used": True,
        "render_manifest": manifest,
        "rubrics": results,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
