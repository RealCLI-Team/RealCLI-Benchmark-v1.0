#!/usr/bin/env python3
"""Author checklist items with a strong "generator" model.

Three tiers, distinguished by what they depend on:

  * **general** — universal checks, no task/artifact input.
  * **task-specific** — required functionality, from the task prompt.
  * **artifact-specific** — behaviours peculiar to THIS implementation, from its
    code/DOM (generated at verify time on the pod).

The model is INJECTED (``model_call``) so this unit-tests without a network. All
output is forced through :func:`checklist.validate_checklist`, so malformed model
output raises ``ValueError`` rather than producing bad items.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from service.verify import checklist

# The model is asked for the FULL canonical field set, not just the text: a short
# `title`, the `checking_content`, the judging `mode`, the `expected_result`, and a
# `weight`. weight lets the author signal that a core requirement matters more than
# a minor one — `scoring` turns the verdict into a weighted fraction, so a generator
# that always emitted 1.0 flattened that signal. All fields have safe defaults in
# `_coerce`, so a model that omits `title`/`weight` still yields valid items.
_SYS_TEMPLATE = (
    "You author BINARY, execution-verifiable check items for a frontend artifact. "
    'Return JSON {{"items":[{{"title","checking_content","mode","expected_result","weight","action_plan","critical"}}...]}}. '
    "Field contract:\n"
    "  - title: a short (<=8 word) summary of what the check verifies.\n"
    "  - checking_content: the precise, objectively checkable condition.\n"
    "  - expected_result: the concrete observable outcome that means PASS.\n"
    "  - mode: one of: {modes}.\n"
    "  - weight: a float in [0.5, 3.0] for how important this check is — 1.0 for a "
    "normal requirement, higher for a core/must-have behavior, lower for a minor/"
    "cosmetic one. Default 1.0 if unsure.\n"
    "  - action_plan: an ORDERED list of interaction steps this check needs to "
    "OBSERVE its outcome — the widgets/keys to exercise. Each step is "
    "{{action, target?, key?, ms?, capture?}}. `action` MUST be one of exactly "
    "these verbs (any other verb is DISCARDED and the check loses its evidence): "
    "click | hover | type | select | press | scroll | wait | mouse_move | drag | "
    "key_sequence | advance | conclude. There is no screenshot/capture/keydown/"
    "keyup/navigate/focus verb — a keypress is `press` (with key and ms for a "
    "held key) or `key_sequence`, and frames are captured via the `capture` "
    "field below, not by a step. Set capture:\"before_after\" on THE STEP THAT "
    "PERFORMS THE INTERACTION — the click, drag, press or key_sequence itself. "
    "The frames are taken immediately before and immediately after THAT step, so "
    "tagging a trailing `wait` captures two frames of an already-finished state "
    "and the check is judged on evidence of nothing happening. Write "
    "{{\"action\":\"drag\",\"target\":\"slider 'Speed'\",\"capture\":\"before_after\"}} "
    "followed by a plain {{\"action\":\"wait\",\"ms\":800}} to let the effect "
    "settle — NEVER {{\"action\":\"wait\",\"capture\":\"before_after\"}} after the "
    "drag. Tag a `wait` only when the passage of time IS the thing being checked "
    "(an animation or simulation that advances on its own with no input). Most "
    "checks need an action_plan; leave it [] only when satisfiable from the first "
    "paint.\n"
    "  - When a step targets a named control, write the target as "
    "role 'Label' using the label EXACTLY as it appears on screen — "
    "slider 'Emission rate', button 'Palette Cryo', checkbox 'Shadows'. Roles: "
    "button | link | slider | switch | checkbox | radio | textbox | tab. A CSS "
    "selector is fine when you can see one in the markup, but never invent pixel "
    "coordinates — a named target is resolved against the live page, a guessed "
    "coordinate lands wherever it lands (usually on a full-screen canvas).\n"
    "  - A DROPDOWN is chosen with ONE `select` step, never two clicks. Write "
    "{{\"action\":\"select\",\"target\":\"combobox 'Lock viewport to device'\","
    "\"value\":\"iPhone 15 Pro · 393\",\"capture\":\"before_after\"}} — `target` "
    "names the dropdown, `value` is the option label exactly as displayed. Do NOT "
    "write click combobox followed by click option: a native <select> renders its "
    "option list outside the page, so an option can never be clicked, and the "
    "control keeps its DEFAULT value while the rest of the plan runs — which "
    "makes a CORRECT artifact look broken for showing the default.\n"
    "  - ORDER THE STEPS SO EACH CONTROL EXISTS WHEN YOU REACH IT. If the code "
    "you were shown disables, hides, or leaves a control empty until some other "
    "control is used (`el.disabled = !state.locked`, a panel rendered only after "
    "a mode is chosen, a list populated on open), put THAT interaction FIRST. A "
    "step aimed at a control that is not yet enabled cannot run, and the steps "
    "after it then measure the control's DEFAULT state — so a CORRECT artifact "
    "is judged on a value the user never asked it to show. Read the code for "
    "the enabling condition rather than assuming the on-screen order is the "
    "usable order.\n"
    "  - critical: true ONLY for a must-pass behavior whose failure makes the "
    "artifact unusable — broken primary navigation, a movement control that "
    "responds in the WRONG direction, a camera that cannot see the play area, "
    "an unsubmittable core form. Default false.\n"
    "  - Do NOT invent a deadline the task never stated. Write the expected "
    "result as the DIRECTION of the change ('fewer meteors spawn', 'the panel "
    "opens'), not as a stopwatch ('the sky is empty within 3 seconds'). A "
    "self-imposed deadline makes a CORRECT artifact fail: setting a spawn rate "
    "to 0 stops new spawns immediately, but the objects already on screen take "
    "as long as they take to fly off, and a 3-second clause turns that physics "
    "into a defect. State a time bound only when the task itself demands one.\n"
    "Each item must be objectively checkable.")


def _coerce(raw_items: list[dict[str, Any]], task_id: str, item_type: str) -> list[dict[str, Any]]:
    out = []
    for i, it in enumerate(raw_items, 1):
        out.append({
            "task_id": task_id,
            "check_item_id": str(i),
            "title": it.get("title", ""),
            "checking_content": it.get("checking_content"),
            "mode": it.get("mode"),
            "item_type": item_type,
            "expected_result": it.get("expected_result"),
            "origin": "generated",
            "weight": it.get("weight", 1.0),
            "critical": bool(it.get("critical", False)),
            "action_plan": it.get("action_plan", []),
        })
    return checklist.validate_checklist(out)  # raises ValueError on malformed items


def _gen(task_id: str, item_type: str, user: str, model_call: Callable,
         allowed_modes: list[str] | None = None) -> list[dict[str, Any]]:
    modes = allowed_modes or sorted(checklist.VALID_MODES)
    sys_prompt = _SYS_TEMPLATE.format(modes=" | ".join(modes))
    obj = model_call([{"role": "system", "content": sys_prompt},
                      {"role": "user", "content": user}])
    items = _coerce(obj.get("items", []), task_id, item_type)
    return checklist.filter_modes(items, allowed_modes)


_INTERACTIVE_SIGNALS = re.compile(
    r"requestAnimationFrame|addEventListener\(\s*['\"](?:keydown|keyup|pointerdown|mousedown)"
    r"|Key[WASD]|Arrow(?:Up|Down|Left|Right)|keyCode|<canvas",
    re.IGNORECASE)


def _is_interactive(task_prompt: str, code: str = "") -> bool:
    """Best-effort signal: does this artifact respond to keyboard/pointer input
    or run an animation loop? Interactive artifacts warrant directional/
    orientation `critical` checks (a control that responds in the WRONG
    direction still 'responds')."""
    blob = f"{task_prompt or ''}\n{code or ''}"
    return bool(_INTERACTIVE_SIGNALS.search(blob))


def gen_general(task_id: str, model_call: Callable,
                allowed_modes: list[str] | None = None) -> list[dict[str, Any]]:
    return _gen(task_id, "general",
                "Author universal checks that apply to ANY web artifact "
                "(loads, no console errors, layout visible, text legible).", model_call,
                allowed_modes=allowed_modes)


def gen_artifact_specific(task_id: str, task_prompt: str, code: str = "", dom: str = "",
                          model_call: Callable | None = None,
                          allowed_modes: list[str] | None = None) -> list[dict[str, Any]]:
    """Author checks grounded in the task AND (when supplied) this artifact.

    This is the single generator for the two non-general tiers. It takes BOTH the
    task prompt and the artifact's code/DOM (or AXTree) because artifact context is
    a strict superset of task-only context — with code/DOM present it authors checks
    for behaviors specific to THIS implementation; with both empty it degrades to
    task-only checks, which is exactly what the old ``gen_task_specific`` produced.
    The emitted ``item_type`` reflects that: "artifact-specific" when grounded in the
    render, "task-specific" when only the prompt was available."""
    grounded = bool((code or "").strip() or (dom or "").strip())
    item_type = "artifact-specific" if grounded else "task-specific"
    user = f"Author checks for the REQUIRED functionality of this task:\n{task_prompt}"
    if grounded:
        user += ("\n\nThe artifact was built as below. Also author checks for behaviors "
                 f"specific to THIS implementation's style.\n\nDOM:\n{dom[:3000]}\n\n"
                 f"Code:\n{code[:4000]}")
    user += ("\n\nFor EACH check, author the concrete widget interactions needed "
             "to OBSERVE its outcome as `action_plan` (interaction-first: exercise "
             "the UI, don't assume from the render).")
    if _is_interactive(task_prompt, code):
        user += ("\n\nThis artifact is INTERACTIVE. Author `critical` checks that "
                 "each core control produces the CORRECT effect DIRECTION and the "
                 "view/camera faces the play area — e.g. the forward key moves the "
                 "player forward (not backward), the left key turns left (not "
                 "right). Ground the intended mapping in the code, but the OBSERVED "
                 "effect (motion is the source of truth) decides PASS: mark such a "
                 "check critical with a before_after action_plan.")
    return _gen(task_id, item_type, user, model_call, allowed_modes=allowed_modes)


def gen_task_specific(task_id: str, task_prompt: str, model_call: Callable,
                      allowed_modes: list[str] | None = None) -> list[dict[str, Any]]:
    """Task-only checks. Thin back-compat wrapper over :func:`gen_artifact_specific`
    (the merged generator) with no artifact context — kept so existing callers
    (engine, Web-UI api, standalone gen, tests) need no change."""
    return gen_artifact_specific(task_id, task_prompt, "", "", model_call,
                                 allowed_modes=allowed_modes)
