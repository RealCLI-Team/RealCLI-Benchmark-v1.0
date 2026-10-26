#!/usr/bin/env python3
"""Agentic judge loop: plan → execute+record → verify.

Drives a browser through a checklist item via planned actions, records the full
trajectory (observation→action→observation), then verifies against the outcome.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from . import browser_actions, judge


def _prompt_text(messages: list[dict], used_frames: list[str]) -> str:
    """Render messages to a readable prompt string (reuse judge module style).

    Image blocks are collapsed to placeholders; screenshots shown separately.
    """
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


def _image_block(path: str) -> dict[str, Any] | None:
    """Build an image block from a file path."""
    try:
        data = base64.b64encode(open(path, "rb").read()).decode("ascii")
    except OSError:
        return None
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}}


def _plan(item: dict[str, Any], obs: dict[str, Any], judge_model_call: Callable,
          vision: bool) -> tuple[dict[str, Any], str]:
    """Build plan messages, call model, return (plan_dict, prompt_text).

    Raises if plan JSON is malformed or missing "plan" key.
    """
    _SYS = ("You are a frontend-verification agent. Given a checklist item and the "
            "current page state (DOM snapshot), produce a JSON plan to verify the check. "
            'Respond with: {"plan": [{"action": "click"|"type"|..., "target": "selector", '
            '"reason": "why"}, ...]}. Actions: click, type, hover, press, scroll, wait, '
            'mouse_move, drag, key_sequence, conclude. End with {"action": "conclude"}.')

    text_parts = [
        f"Check: {item['checking_content']}",
        f"Expected result: {item['expected_result']}",
        f"DOM snapshot:\n{obs.get('dom_snapshot', '')}"
    ]

    image_blocks: list[dict] = []
    used_frames: list[str] = []
    if vision and obs.get("screenshot"):
        screenshot_path = obs["screenshot"]
        blk = _image_block(screenshot_path)
        if blk is not None:
            image_blocks.append(blk)
            used_frames = [os.path.basename(screenshot_path)]
            text_parts.append("Screenshot is attached below.")

    text = "\n\n".join(text_parts)
    if image_blocks:
        user_content: Any = [{"type": "text", "text": text}, *image_blocks]
    else:
        user_content = text

    messages = [
        {"role": "system", "content": _SYS},
        {"role": "user", "content": user_content}
    ]
    prompt = _prompt_text(messages, used_frames)

    obj = judge_model_call(messages)

    # Strict: missing or empty "plan" key raises
    if "plan" not in obj:
        raise KeyError('plan key missing')
    plan_list = obj.get("plan")
    if not plan_list:
        raise ValueError('plan is empty')

    return obj, prompt


def _verify(item: dict[str, Any], steps: list[dict[str, Any]],
            judge_model_call: Callable, vision: bool) -> tuple[dict[str, Any], str]:
    """Build verify messages from trajectory, call model, return (result, prompt_text).

    Handles non-boolean satisfied as None (indeterminate).
    """
    _SYS = ("You are a strict frontend-artifact verifier. Given a single binary check "
            "and an execution trace, decide if the check was SATISFIED. Be concrete and "
            "cite what you actually observed in the trace. Respond with: "
            '{"satisfied": true|false, "rationale": "one or two sentences citing evidence"}.')

    # Build trace text from steps
    trace_lines = []
    for step in steps:
        if "error" in step:
            trace_lines.append(f"Step {step['step_no']}: {step['action']} on {step['target']} "
                             f"→ ERROR: {step['error']}")
        else:
            trace_lines.append(f"Step {step['step_no']}: {step['action']} on {step['target']} "
                             f"→ {step.get('result', 'N/A')}")
            if "observation" in step:
                trace_lines.append(f"  Observation: {step['observation'].get('dom_snapshot', '')[:200]}")

    text_parts = [
        f"Check: {item['checking_content']}",
        f"Expected result: {item['expected_result']}",
        f"Execution trace:\n" + "\n".join(trace_lines)
    ]

    image_blocks: list[dict] = []
    used_frames: list[str] = []
    if vision:
        # Collect screenshots from step observations
        for i, step in enumerate(steps):
            if step.get("observation", {}).get("screenshot"):
                screenshot_path = step["observation"]["screenshot"]
                blk = _image_block(screenshot_path)
                if blk is not None:
                    image_blocks.append(blk)
                    used_frames.append(f"step_{i}.png")
        if image_blocks:
            text_parts.append(f"Screenshots ({len(image_blocks)}) are attached below.")

    text = "\n\n".join(text_parts)
    if image_blocks:
        user_content: Any = [{"type": "text", "text": text}, *image_blocks]
    else:
        user_content = text

    messages = [
        {"role": "system", "content": _SYS},
        {"role": "user", "content": user_content}
    ]
    prompt = _prompt_text(messages, used_frames)

    obj = judge_model_call(messages)

    # Handle non-bool satisfied → None
    sat = obj.get("satisfied")
    satisfied = bool(sat) if isinstance(sat, bool) else None
    rationale = str(obj.get("rationale", ""))[:800]

    return {"satisfied": satisfied, "rationale": rationale}, prompt


def run_item(item: dict[str, Any], page: Any, observe: Callable,
             judge_model_call: Callable, *, out_dir: Path, vision: bool,
             max_plan_steps: int = 12) -> dict[str, Any]:
    """Run a single checklist item through plan → execute → verify.

    Args:
        item: Checklist item dict with task_id, check_item_id, checking_content, etc.
        page: Playwright page-like object.
        observe: Function taking page and returning {"dom_snapshot": str, "screenshot": path|None}.
        judge_model_call: Function taking messages, returning {"plan": [...]} or {"satisfied": bool, ...}.
        out_dir: Directory to write output.
        vision: Whether to attach screenshots to prompts.
        max_plan_steps: Max actions to execute.

    Returns:
        {"satisfied": bool|None, "rationale": str, "trajectory_ref": item_id, "steps_taken": int}

    Writes:
        out_dir/agent/<item_id>/plan.json - the plan returned by model
        out_dir/agent/<item_id>/steps.json - full trajectory + verify result
        out_dir/agent/<item_id>/step_*.png - screenshots if observe returns paths
    """
    item_id = item["check_item_id"]

    try:
        # Observe current state
        obs = observe(page)

        # Plan: call model to get plan
        try:
            plan_obj, plan_prompt = _plan(item, obs, judge_model_call, vision)
        except (KeyError, ValueError) as e:
            # Bad plan → indeterminate
            return {
                "satisfied": None,
                "rationale": "plan generation failed",
                "trajectory_ref": item_id,
                "steps_taken": 0
            }

        # Execute planned actions
        plan_list = plan_obj.get("plan", [])
        steps: list[dict[str, Any]] = []
        step_no = 0

        for action in plan_list:
            if step_no >= max_plan_steps:
                break

            action_type = action.get("action")

            # Record the action
            step_record: dict[str, Any] = {
                "step_no": step_no,
                "action": action_type,
                "target": action.get("target", ""),
                "reason": action.get("reason", "")
            }

            # Skip "conclude" from execution; just record and stop
            if action_type == "conclude":
                steps.append(step_record)
                break

            # Execute the action
            result = browser_actions.execute(page, action)

            # Record result or error
            if "error" in result:
                step_record["error"] = result["error"]
            else:
                step_record["result"] = result.get("result", "ok")

            # Always observe after action (even on error)
            obs_after = observe(page)
            step_record["observation"] = obs_after

            steps.append(step_record)
            step_no += 1

            # Stop at conclude
            if result.get("result") == "conclude":
                break

        # Verify: build trajectory and call verify
        verify_result, verify_prompt = _verify(item, steps, judge_model_call, vision)

        # Write outputs
        out_base = Path(out_dir) / "agent" / item_id
        out_base.mkdir(parents=True, exist_ok=True)

        # Write plan.json
        with open(out_base / "plan.json", "w") as f:
            json.dump(plan_obj, f, indent=2)

        # Write screenshots from steps. The observation records the ephemeral
        # temp path the browser wrote to; rewrite it to the persisted basename
        # (step_<i>.png) so the UI/frame route can resolve it. Only rewrite on a
        # successful copy — a missing source leaves the field untouched.
        for i, step in enumerate(steps):
            if step.get("observation", {}).get("screenshot"):
                screenshot_path = step["observation"]["screenshot"]
                try:
                    dest = out_base / f"step_{i}.png"
                    shutil.copy(screenshot_path, dest)
                    step["observation"]["screenshot"] = f"step_{i}.png"
                except OSError:
                    pass  # screenshot file may not exist in test stubs

        # Write steps.json with full metadata
        steps_output = {
            "item_id": item.get("check_item_id"),
            "task_id": item.get("task_id"),
            "checking_content": item.get("checking_content"),
            "mode": item.get("mode"),
            "expected_result": item.get("expected_result"),
            "plan_prompt": plan_prompt,
            "plan": plan_obj.get("plan", []),
            "steps": steps,
            "verify_prompt": verify_prompt,
            "satisfied": verify_result["satisfied"],
            "rationale": verify_result["rationale"],
            "steps_taken": step_no
        }
        with open(out_base / "steps.json", "w") as f:
            json.dump(steps_output, f, indent=2)

        return {
            "satisfied": verify_result["satisfied"],
            "rationale": verify_result["rationale"],
            "trajectory_ref": item_id,
            "steps_taken": step_no
        }

    except Exception as e:
        # Any uncaught exception → indeterminate
        return {
            "satisfied": None,
            "rationale": f"agent error: {str(e)[:200]}",
            "trajectory_ref": item_id,
            "steps_taken": 0
        }


def run_session(items: list[dict[str, Any]], entry_url: str, page: Any,
                judge_model_call: Callable, *, out_dir: Path,
                judge_model_call_lang: Callable | None = None) -> list[dict[str, Any]]:
    """Run multiple items, reloading entry_url between them.

    Args:
        items: List of checklist items.
        entry_url: URL to navigate to before each item.
        page: Playwright page-like object.
        judge_model_call: Model call for the vision (VLM-Based) items.
        out_dir: Output directory.
        judge_model_call_lang: Model call for the text-only (LLM-Based) items.
            Falls back to ``judge_model_call`` when None, so a single-model run
            is unchanged.

    Returns:
        List of results from run_item.
    """
    lang_call = judge_model_call_lang or judge_model_call
    results = []
    for item in items:
        # Reload entry URL
        page.goto(entry_url)

        # Determine vision mode from item; VLM items see screenshots, LLM items
        # reason over the trace and use the language judge.
        vision = item.get("mode", "").startswith("VLM")
        item_call = judge_model_call if vision else lang_call

        # Real observe implementation: capture DOM snapshot and screenshot
        def default_observe(p):
            obs = {"dom_snapshot": "", "screenshot": None}

            # Capture DOM snapshot: try evaluate() for text summary + content() for full HTML
            try:
                # Try to get text content via innerText
                text_content = ""
                try:
                    text_content = p.evaluate("() => document.body ? document.body.innerText : ''")
                except Exception:
                    pass

                # Try to get full HTML
                html_content = ""
                try:
                    html_content = p.content()
                except Exception:
                    pass

                # Build snapshot: prioritize structured content, cap at ~6000 chars
                snapshot = ""
                if text_content:
                    snapshot = text_content[:4000]
                elif html_content:
                    snapshot = html_content[:4000]

                obs["dom_snapshot"] = snapshot
            except Exception:
                # Defensive: any failure → empty snapshot, continue
                pass

            # Capture screenshot to temp file
            try:
                fd, tmp_path = tempfile.mkstemp(suffix=".png")
                os.close(fd)  # Close the file descriptor
                p.screenshot(path=tmp_path)
                obs["screenshot"] = tmp_path
            except Exception:
                # Defensive: screenshot failure → None, continue
                pass

            return obs

        # Run item
        result = run_item(item, page, default_observe, item_call,
                         out_dir=out_dir, vision=vision)
        results.append(result)

    return results
