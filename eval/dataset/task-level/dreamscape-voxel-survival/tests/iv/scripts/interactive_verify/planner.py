#!/usr/bin/env python3
"""Stage 1 — single-pass action planner.

Given the rendered page (initial DOM + screenshot) and the task's checklist of
interaction scripts, produce ONE ordered list of atomic browser actions that
exercises everything the checklist needs to observe. This is the paper's
checklist-guided action planning: one plan per page, not per item.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Callable

from scripts.interactive_verify._images import image_block
from scripts.interactive_verify import plan_grounding

ALLOWED_ACTIONS = frozenset({
    "click", "hover", "type", "select", "press", "scroll", "wait",
    "mouse_move", "drag", "key_sequence", "advance", "conclude",
    # DEFECT 97: reaching another page of a multi-page artifact. Without it the
    # only way across was to guess a nav link's anchor text; see
    # `executor.resolve_same_origin` for the measurement that motivated it.
    "goto",
})

# The checklist headings shown to the planner are rendered `[item 4]`, and the
# prompt asks the model to keep `for_item` on directional steps — so the model
# routinely stamps `for_item: "item 4"` (or `"#4"`) while the deterministic
# back-fill stamps the bare `"4"`. Those are the SAME item, but they hash to
# different keys in the scorer's `pair_map`, orphaning the model's real evidence
# (and letting the back-filled, possibly-never-executed step win). Normalize
# every `for_item` to the bare id string at the boundary where it enters the
# trajectory so both forms join.
_ITEM_ID_RE = re.compile(r"^\s*(?:item\s*|#\s*)+(?P<id>\d[\w.-]*)\s*$", re.IGNORECASE)


def normalize_item_id(value: Any) -> Any:
    """Bare-id form of a `for_item` / `check_item_id` label ("item 4" -> "4").

    Only strips an `item`/`#` prefix when what follows starts with a DIGIT (ids
    are `str(i)` from the generator's `_coerce`), so an id that merely begins
    with those letters is left alone rather than mangled."""
    if value is None:
        return None
    s = str(value).strip()
    m = _ITEM_ID_RE.match(s)
    return m.group("id") if m else s


# The visible LABEL inside any selector dialect. The checklist author writes
# `slider 'Symmetry'` and the planner writes `input[type=range]:has-text("Symmetry")`
# for the same control; both name the same widget, but they are different
# strings, so a dedup keyed on the raw (or even the normalized) selector treats
# them as two distinct probes. On b0811 that let the back-fill re-click a toggle
# the model had already clicked — flipping the feature back OFF between the
# before and after frames — on 395 of 5,088 probes (7.8%), every one a click.
_LABEL_RES = (
    re.compile(r""":has-text\(\s*["']([^"']*)["']\s*\)"""),
    re.compile(r"""^\s*[a-z]+\s+["']([^"']*)["']\s*$""", re.IGNORECASE),
    re.compile(r"""\[aria-label=["']([^"']*)["']\]"""),
    re.compile(r""":text\(\s*["']([^"']*)["']\s*\)"""),
)

# DEFECT 69: the same control, written the other way round. The generator
# authors `AIRFOIL button` — label first, role as an unquoted trailing suffix —
# while the planner writes `button:has-text("AIRFOIL")` for the identical
# widget. Rule 2 above only reads the AX order (`button 'AIRFOIL'`), so these
# two hashed to 'airfoil button' and 'airfoil' and the defect-66 adoption pass
# could not see that the planner had ALREADY covered the item. It therefore
# appended the prose as a fresh probe, which Playwright cannot resolve: on
# `mKoh6BY` items 2, 7, 10 and 11 all errored and determinacy fell 9/12 -> 6/12.
#
# Stripping the role suffix here is what makes adoption possible, and adoption
# is the better outcome even though `executor.normalize_selector` can now run
# the prose too: adopting reuses a selector already proven to resolve on THIS
# page, in the sequence the planner chose, whereas a rewrite is a fresh guess
# appended at the end. Anchored and applied only to a role-suffixed string, so
# `button:has-text("Reset")` (no trailing bare word) is untouched.
_ROLE_SUFFIX_RE = re.compile(
    r"^(?P<txt>.+?)\s+(?:button|btn|checkbox|check|slider|range|link|switch"
    r"|toggle|radio|textbox|input|field|combobox|dropdown|select|tab|option"
    r"|control|icon|menu)s?$", re.IGNORECASE)

# Enough selector syntax to prove a string is not prose. Deliberately narrower
# than `executor._CSS_ISH`: this one must NOT treat a bare word as CSS, because
# `SPIN button` has to reach the role-suffix strip above. `\.\w` is unanchored
# for the same reason as `executor._PROSE_BAD` — `div.panel input` would
# otherwise strip to `div.panel` and merge two structurally different probes.
_CSS_ISH_TGT = re.compile(r"""["'#\[\]>:()=,*~+|]|\.\w|\s>>\s"""
                          r"""|^\s*(?:xpath|text|css|id)\s*=""")


# --- Drag direction ---------------------------------------------------------
#
# A `drag` step carries a target but no coordinates, and the executor fills them
# by sweeping across the element's box (see `executor._resolve_coord_action`).
# That sweep was unconditionally LEFT-TO-RIGHT, i.e. every slider was driven to
# its MAXIMUM — no matter what the step was for. On b0811 `Dfxc2vB` the planner
# authored three separate intents against one control:
#     step 6  "Drag the meteor shower rate slider to 100."   (item 1)
#     step 8  "Drag the meteor shower rate slider to 0."     (item 2)
#     step 10 "Restore meteor shower rate to a moderate level."
# and all three executed as the identical sweep to 92 st/s. Item 1 passed; item 2
# ("slider must be able to reach 0") was judged False with a rationale that is
# exactly right about what it saw — "the slider value remained at 92 st/s ...
# never successfully moved to 0" — and, being critical, gated the artifact from
# 0.903 to 0.000. `vJJzx4N` failed the same way on "negative velocity reverses
# flow" (0.808 -> 0.000).
#
# This is the worst class of verifier bug: the item is UNFALSIFIABLE. No artifact
# can satisfy "the slider reaches zero" when the harness only ever drags to max,
# so the check measures the verifier, not the artifact — and it reports a
# confident, specific, well-evidenced False either way.
#
# So the direction has to be part of the step. We infer it from the text that
# states the intent (the planner's own note, or the item's checking_content /
# expected_result for a back-filled probe) and stamp it on the action; the
# executor reverses the sweep for a decrease. Inference is deliberately
# conservative — an unrecognized phrasing yields None and keeps the old
# increase-by-default behaviour, which is right for the large majority of probes.
_DECREASE_RE = re.compile(
    r"\b(?:to\s+(?:0|zero)\b|down\s+to\s+0\b|reach(?:es|ing)?\s+(?:0|zero)\b"
    r"|minimum|min\b|lowest|leftmost|leftward"
    r"|(?:to|toward|towards)\s+the\s+left\b|all\s+the\s+way\s+left\b"
    r"|negative|decreas\w*|reduc\w*|lower\w*"
    r"|slow\w*\s+down|turn(?:s|ing)?\s+(?:it\s+)?down|dial\w*\s+down)",
    re.IGNORECASE)
_INCREASE_RE = re.compile(
    r"\b(?:to\s+(?:100|max\w*)\b|maximum|highest|rightmost|rightward"
    r"|(?:to|toward|towards)\s+the\s+right\b|all\s+the\s+way\s+right\b"
    r"|increas\w*|rais\w*|higher|speed\w*\s+up"
    r"|turn(?:s|ing)?\s+(?:it\s+)?up|dial\w*\s+up)",
    re.IGNORECASE)

# DEFECT 61: the generic verbs above describe whatever the sentence is about,
# and for a slider item that is usually the READOUT, not the control.
#
#     "The REYNOLDS NO. value (initially 61) increases as the viscosity
#      slider is moved left toward the 'thin' end."
#
# `_INCREASE_RE` matches `increases` -- which is true of the Reynolds number and
# false of the slider -- and returns "increase". The harness then dragged
# viscosity 0.020 -> 0.090, the opposite of what the item required, and the judge
# wrote the defect up itself: "the viscosity slider was dragged toward 'thick',
# not 'thin' ... the required interaction was never performed". Unfalsifiable in
# the same way the original left-to-right sweep was, and for the same reason: the
# harness cannot perform the interaction the item is about.
#
# An inverse relationship makes this systematic rather than unlucky. Whenever a
# check reads "X goes UP as the slider goes DOWN", the two clauses disagree and
# the generic verb picks the wrong one -- so the more confidently the item is
# written, the more reliably it is mis-executed.
#
# So a phrase describing the CONTROL'S OWN MOTION takes precedence over any
# verb elsewhere in the sentence. The motion verb must be adjacent to the
# direction word (within three tokens) so that a bare "the left panel" or a
# stray "right" is not read as an instruction.
_MOTION_RE = re.compile(
    r"\b(?:mov\w*|drag\w*|slid\w*|push\w*|pull\w*|swip\w*|set|adjust\w*)"
    r"(?:\s+\w+){0,3}?\s+(?:to\s+|toward\s+|towards\s+)?(?:the\s+)?"
    r"(left|right)\b",
    re.IGNORECASE)


def _infer_motion_direction(text: str) -> str | None:
    """Direction from an explicit statement of the control's own movement.

    Returns None when the text names both directions -- "drag left, then drag
    right" is a range sweep, and guessing either end re-creates the bug the
    conservative default exists to avoid.
    """
    found = {("decrease" if m.group(1).lower() == "left" else "increase")
             for m in _MOTION_RE.finditer(text or "")}
    return found.pop() if len(found) == 1 else None


def infer_drag_direction(text: Any) -> str | None:
    """``"decrease"`` / ``"increase"`` / ``None`` for a step's stated intent.

    Only returns ``"decrease"`` when the text asks for a decrease and does NOT
    also ask for an increase. Mixed phrasings ("increase then decrease", or an
    item describing a slider's whole range) are ambiguous, and guessing wrong
    there re-creates the bug in the opposite direction — so they yield None and
    the caller keeps the default sweep.

    An explicit statement of the CONTROL'S motion ("moved left", "drag it to
    the right") wins over the generic verbs, which describe whatever the
    sentence is about and for a slider item are usually about the readout. See
    DEFECT 61 and `_MOTION_RE`."""
    if not text:
        return None
    s = str(text)
    motion = _infer_motion_direction(s)
    if motion:
        return motion
    dec = bool(_DECREASE_RE.search(s))
    inc = bool(_INCREASE_RE.search(s))
    if dec and not inc:
        return "decrease"
    if inc and not dec:
        return "increase"
    return None


def stamp_drag_directions(actions: list[dict[str, Any]],
                          items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every coordinate-less ``drag`` an explicit ``direction``.

    Two sources, most-specific first:

    1. the step's own ``note`` — the planner writes the intent there verbatim
       ("Drag the meteor shower rate slider to 0."), and when one plan drags the
       SAME control for several items this is the only thing that distinguishes
       them;
    2. the ``for_item`` item's ``checking_content`` + ``expected_result``, which
       is what a back-filled probe has (its note is just
       "[auto: directional probe]").

    A step that already carries a direction, or coordinates, is left alone."""
    by_id: dict[Any, dict[str, Any]] = {}
    for it in items or []:
        by_id[normalize_item_id(str(it.get("check_item_id", "")))] = it
    out: list[dict[str, Any]] = []
    for a in actions or []:
        if (a.get("action") != "drag" or a.get("direction")
                or a.get("from") or a.get("to")):
            out.append(a)
            continue
        d = infer_drag_direction(a.get("note"))
        if d is None:
            it = by_id.get(normalize_item_id(a.get("for_item")))
            if it is not None:
                d = infer_drag_direction(
                    f"{it.get('checking_content', '')} {it.get('expected_result', '')}")
        out.append({**a, "direction": d} if d else a)
    return out


def target_label(target: Any) -> str | None:
    """The human-visible label a selector names, across dialects, or None.

    Used as a dedup key, never to build a selector — two steps naming the same
    label are the same probe however each spelled it."""
    if not target:
        return None
    t = str(target).strip()
    for rx in _LABEL_RES:
        m = rx.search(t)
        if m and m.group(1).strip():
            return m.group(1).strip().lower()
    # DEFECT 69: no quoted label anywhere, so this may be bare prose. Strip a
    # trailing role word (`AIRFOIL button` -> `airfoil`) so it hashes to the same
    # key as the planner's `button:has-text("AIRFOIL")`. Only for a string with
    # no selector syntax: a real CSS selector ending in a bare tag name
    # (`div.panel input`) names a control by STRUCTURE, and truncating it to
    # `div.panel` would silently merge two different probes.
    if not _CSS_ISH_TGT.search(t):
        m = _ROLE_SUFFIX_RE.match(t)
        if m and m.group("txt").strip():
            return m.group("txt").strip().lower()
    return t.lower() or None


# The driving browser context is created with no `viewport` (see
# `run.py:factory`), so it is Playwright's default 1280x720. The plan model is
# never told this and routinely invents coordinates for a page it imagines
# taller: on b0811, 213 of 1,352 coordinate steps (15.8%) pointed outside the
# box, y running to 1040, and 183 of those were the before/after probe itself.
# A drag to y=815 does not move the slider the model meant — it lands on
# whatever full-screen canvas is underneath, which is exactly the "DENSITY and
# BLUR are altered" the reviewer saw where a Symmetry slider was the target.
DRIVE_VP = (1280, 720)


def _in_viewport(pt: Any) -> bool:
    """True if `pt` is a usable [x, y] inside the driving viewport."""
    if not isinstance(pt, (list, tuple)) or len(pt) < 2:
        return False
    x, y = pt[0], pt[1]
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return False
    return 0 <= x < DRIVE_VP[0] and 0 <= y < DRIVE_VP[1]


_SYS = (
    "You are a frontend-verification action planner. You are given a rendered web "
    "page (an initial screenshot, a DOM snapshot, and an inventory of the controls "
    "that actually exist on each of the artifact's pages), the build task, and a "
    "checklist of interaction scripts (each with Preconditions, numbered Steps "
    "with expected results, and Pass criteria). Produce ONE ordered list of atomic "
    "browser actions that, executed in sequence from the initial page state, "
    "exercises every interaction the checklist needs to observe. Prefer stable CSS "
    "selectors from the DOM. Insert short waits after actions that trigger "
    "animation or async loads.\n"
    "INTERACTIVE ELEMENTS PRESENT lists what was actually rendered on each page, "
    "as `role \"accessible name\"` with any state. Use it: when a listed control "
    "fits a checklist item, aim at its EXACT name rather than a name you recall "
    "from a similar product, and match the verb to the ROLE — slider/sliderthumb "
    "take drag (name the target, no coordinates), combobox/listbox take ONE "
    "select{target,value}, checkbox/switch/radio/tab/button take click, "
    "textbox/searchbox take type. A page whose only listed nodes are canvas or "
    "video exposes no clickable DOM: drive it with the keyboard. Controls listed "
    "under a page other than the entry page are NOT reachable from the start — "
    "goto{url} that page first. The inventory is captured at LOAD time, so "
    "controls that appear only after an interaction (modal contents, later "
    "wizard screens, an unopened tab panel) are legitimately absent from it; "
    "absence there is not proof they do not exist, so keep chaining speculative "
    "follow-on steps where the checklist implies them.\n"
    # Games are the hard case: the landing screen is almost always a title/menu or
    # a loading screen, and the real content lives behind a Start action and is
    # driven by the KEYBOARD on a <canvas> that exposes no clickable DOM. A plan
    # that never starts the game scores a blank menu, so make starting + driving
    # explicit rather than leaving the model to infer it.
    "IF THIS IS A GAME (canvas-based, a Start/Play/ENGAGE/RACE button, a title or "
    "loading screen, or the task describes gameplay): FIRST get past the "
    "title/menu — click the visible Start/Play/Begin control, and if none is "
    "obvious also `press` Enter and then Space (start screens commonly bind one of "
    "these). Insert a wait so the game boots.\n"
    # The most common game-verification failure is a MULTI-STEP setup funnel. This
    # guidance must be PROMPT TEXT, not a comment: it was previously commented out,
    # so the funnel instruction never reached the model and the "you switch to the
    # keyboard" sentence below dangled with no lead-in.
    "The most common game-verification failure is a MULTI-STEP setup funnel: the "
    "first Start button ('BEGIN CULTURE', 'NEW GAME', 'PLAY') often opens a "
    "config/lobby screen — pick circuit/car/difficulty, a 'STEP 01 / 02' wizard — "
    "and the race/level only launches after a FINAL confirm click ('TO THE GARAGE', "
    "'DEPLOY', 'DROP IN', 'START RACE', 'BEGIN', 'GO', 'RACE', 'CONTINUE', 'NEXT', "
    "'LAUNCH', 'CONFIRM'). You plan from the FIRST screen and cannot see these later "
    "screens, so you must SPECULATIVELY chain the whole funnel: after the first "
    "Start, emit several more clicks targeting these launch/advance labels (a click "
    "whose selector matches nothing is skipped in ~0ms, so extra guesses are cheap), "
    "each followed by a short wait, to walk from title → setup → step 2 → active "
    "gameplay. Only AFTER this funnel should "
    "you switch to the keyboard. Do NOT fire movement keys while a setup/menu "
    "screen is still up — keys pressed on the lobby do nothing and the run scores "
    "a blank menu (the single biggest scoring bug observed).\n"
    "THEN actually PLAY: canvas games take "
    "no DOM clicks, so drive them with the keyboard — use key_sequence with the "
    "likely controls (Arrow keys and W/A/S/D for movement, Space for "
    "jump/brake/boost, Enter to confirm) held across several steps, with short "
    "waits between, so the trajectory captures real gameplay (motion, HUD/score "
    "changes) and not just the menu. Consult the task and checklist for the "
    "specific controls.\n"
    # DEFECT 126 / report fault 3a. First-person and orbit canvas games capture the
    # mouse through the Pointer Lock API, which the browser grants ONLY on a click
    # INSIDE the canvas (the required user gesture). Once locked the cursor is hidden
    # and the game reads RELATIVE motion (movementX/Y) as look/aim, so the camera is
    # driven by mouse_move, not the keyboard. The click that grabs the lock usually
    # throws the view straight up to blank sky; the judge then scores an empty sky
    # frame as "nothing rendered" even though the scene is intact (measured on
    # coastal-town-voxel-reconstruction and samurai-shrine-dungeon-crawler). The
    # recovery has to be planned explicitly -- the model never sees the flung frame.
    "IF THE GAME USES MOUSE-LOOK / POINTER LOCK (a first-person or orbit camera, a "
    "crosshair, a 'click to look'/'click to play' canvas, or WASD+mouse controls): "
    "after starting, CLICK the canvas once to acquire pointer lock, then AIM with "
    "mouse_move. mouse_move{x,y} moves the pointer to the ABSOLUTE viewport pixel "
    "x,y (x in 0..1279, y in 0..719); under pointer lock the game sees only the "
    "DELTA from the previous pointer position -- movementX turns the view "
    "left/right, movementY pitches it up/down -- so ONE move barely turns and you "
    "must emit a SHORT SEQUENCE of mouse_move steps to sweep the camera. Because "
    "grabbing the lock flings the pitch up to the sky, your FIRST moves after the "
    "click must bring the horizon back: step the pointer DOWNWARD in modest "
    "increments (e.g. mouse_move y=360 then 480 then 600, each ~120px) so the scene "
    "(terrain, level, objects) is in frame BEFORE any judged capture, then add a "
    "short wait. Continue with WASD/arrows for movement and further mouse_move steps "
    "to look around. If the game does NOT lock the pointer -- a free orbit scene you "
    "rotate by dragging empty canvas -- use drag{from:[x,y],to:[x,y]} across the "
    "canvas instead: it presses at `from` and sweeps to `to`.\n"
    "Respond with ONLY a JSON object: "
    '{"actions": [{"action": "<verb>", ...params, "note": "why"}, ...]}. '
    "Allowed verbs and their params: click{target}, hover{target}, type{target,text}, "
    "select{target,value}, "
    "press{key,hold_ms?}, scroll{dx,dy}, wait{ms}, mouse_move{x,y}, drag{from:[x,y],to:[x,y]}, "
    "key_sequence{keys:[...],hold_ms?,delay_ms?}, advance{}, conclude{}, goto{url}. "
    # DEFECT 97. The measured failure was not a missing link but a ONE-WAY trip:
    # on `zxtyfcx` the planner's `a:has-text("Fluid Particles")` selectors were
    # correct FOR THE HOME PAGE, and each was run after an earlier click had
    # already navigated to a sub-page. Its three attempts to get back
    # (`a:has-text("david.li")`) each dead-waited the full cap and failed, because
    # sub-pages carry no such link -- so 9 of 9 item-bearing steps errored and the
    # two items that survived scored the task reward=1.0000.
    "MULTI-PAGE ARTIFACTS: a click on a link NAVIGATES, and the page you land on "
    "is NOT the page whose DOM you were shown — every selector you wrote from that "
    "DOM will miss until you go back. Use goto{url} to move between pages: it is "
    "addressed, not guessed, and cannot be defeated by an unexpected layout. "
    "Return to the entry with goto{url:\"<the entry page>\"} BEFORE the first step "
    "of any item that targets the landing page, and go straight to a sub-page with "
    "goto{url:\"<its path>\"} instead of hunting for the link that reaches it. "
    "The url is relative to the current page and MUST stay inside the artifact; an "
    "off-site url is refused. Never `goto` an external site named in the page. "
    # A native <select> is the one control the click vocabulary cannot drive: its
    # option list is painted by the OS, so Playwright refuses to click an
    # <option> and page.fill rejects a <select>. Planning the two-click form a
    # human performs costs two ~15s dead-waits AND leaves the control at its
    # default, which the judge then reads as the artifact showing a wrong value
    # (`4ewUptv`: 1.000 -> 0.000). collapse_select_pairs repairs the pattern after
    # the fact; saying so here avoids emitting it at all.
    "Choose a dropdown option with ONE select{target,value} — target names the "
    "dropdown (combobox 'Lock viewport to device'), value is the option label as "
    "displayed. NEVER click a combobox and then click an option. "
    # advance{} auto-finds and clicks the on-screen 'go deeper' / launch button
    # (Start, Next, To the Garage, Enter the Circuit, ...) even below the fold —
    # use it to cross a multi-step setup wizard when you don't know the exact
    # button labels of screens that only appear after the first click. It no-ops
    # harmlessly once gameplay is reached.
    "Use advance{} to step through a multi-screen game setup/lobby to reach "
    "gameplay when the later screens' buttons are unknown. "
    # hold_ms turns an instant key tap into a held press — for canvas games the
    # car/player only moves while a key is HELD, so use hold_ms ~300-800 (and
    # delay_ms between keys) when driving gameplay, not a bare tap.
    "For gameplay use hold_ms (e.g. 500) on press/key_sequence so movement keys are "
    "held long enough to produce visible motion. "
    "Each checklist item carries an `action_plan` of steps; some steps are tagged "
    "`capture:\"before_after\"` (their effect is judged by comparing the frame "
    "before vs after) and `for_item:<id>`. When you emit the action for such a "
    "step, KEEP both tags on it — and keep the step's `target` VERBATIM. "
    "The viewport is exactly 1280x720; any coordinate outside it is discarded. "
    "To move a slider, ALWAYS emit drag{target} naming the control "
    "(the item's own target, e.g. \"slider 'Emission rate'\", or a CSS selector "
    "from the DOM snapshot) and NO from/to — the executor measures the live "
    "element and sweeps across it. A drag at guessed coordinates lands on "
    "whatever is at that pixel, which on these pages is the full-screen canvas, "
    "and the check is then judged on the canvas reacting instead of the slider "
    "moving. Only use from/to when there is genuinely no element to name (e.g. "
    "orbiting a 3D scene by dragging empty canvas). "
    "Put `capture:\"before_after\"` on the interaction step itself, never on a "
    "wait that follows it. "
    "A comma-separated selector resolves to the FIRST MATCH IN DOCUMENT ORDER, "
    "not to the first clause that matches, and has-text is a SUBSTRING match. So "
    "never add a one- or two-character fallback clause: "
    "`button:has-text(\"RESET\"), button:has-text(\"R\")` clicks a \"PARAMS\" "
    "button that happens to sit earlier in the DOM. Name the control once, with "
    "its full visible label. "
    "End the list with {\"action\": \"conclude\"}."
)


class PlanError(RuntimeError):
    """Raised when the planner produces no usable action list."""


def _checklist_text(items: list[dict[str, Any]]) -> str:
    lines = []
    for it in items:
        title = it.get("title")
        head = f"[item {it.get('check_item_id')}]"
        if title:
            head += f" {title}:"
        lines.append(f"{head} {it.get('checking_content')}\n"
                     f"  expected: {it.get('expected_result')}")
    return "\n".join(lines)


# The argument each verb's handler reads with a RAW SUBSCRIPT, and the synonyms
# the generator writes instead. `_action_key_sequence` does `action["keys"]`, so a
# step authored `{"action":"key_sequence","key":"w"}` -- singular, which is how the
# `press` verb one line above it in the same prompt spells the same idea -- raises
# `KeyError: 'keys'` and the interaction never happens.
#
# The verb and the argument name are authored TOGETHER by one model in one reply,
# and it mixes the two spellings freely: 45 singular `key` on key_sequence and 1
# plural `keys` on press in 400 b0811 tasks. Nothing downstream could tell the
# difference between "the model meant a key it named" and "the model named no key
# at all", because both arrive as the same bare KeyError -- so 51 key_sequence
# steps died carrying a perfectly good key.
#
# Repair here, at the boundary, rather than in the handlers: `browser_actions` is
# shared with the web UI verify service, and a step whose argument is genuinely
# ABSENT must still raise. This maps a present-but-misnamed argument onto the name
# its handler reads and leaves a truly empty step to fail as before (which
# degrades its item to indeterminate -- DEGRADE-NEVER-GATE, v0.6).
_ARG_SYNONYMS: dict[str, tuple[str, tuple[str, ...]]] = {
    "key_sequence": ("keys", ("key",)),
    "press": ("key", ("keys",)),
    # `key` on a `type` step is the generator conflating the two keyboard verbs:
    # 11 of 400 b0811 plans wrote `{"action":"type","target":...,"key":"..."}`
    # meaning the text to enter. Taken last, so a real `text`/`value` always wins.
    "type": ("text", ("value", "content", "key")),
    "wait": ("ms", ("ms_wait", "duration_ms", "timeout_ms")),
    # DEFECT 97. A model that has just been told the file inventory naturally
    # writes the path under whichever key reads best -- `path`, `href`, `to`, or
    # (because every other verb takes one) `target`.
    "goto": ("url", ("path", "href", "to", "target")),
}

# Some plans wrap every argument in an envelope -- `{"action":"key_sequence",
# "params":{"keys":[...],"hold_ms":300}}` -- which is a shape no handler reads, so
# ALL of the step's arguments vanish at once, not just one. 25 of 400 b0811 plans
# did this on key_sequence alone. Flattening is safe because the envelope only
# ever holds handler arguments; a key already present at the top level wins, since
# that is the level the handler actually reads.
_ARG_ENVELOPES = ("params", "args", "arguments", "options")

# DEFECT 48. `_action_scroll` reads BOTH `action["dx"]` and `action["dy"]` as raw
# subscripts, and the plan model almost never writes either: measured over the
# 3,034 b0811 v011 plans, 698 of 1,171 scroll steps (59.6%) are missing one or
# both, and 332 scroll steps died in execution with the bare error `'dx'`.
# The shapes it writes instead, by frequency:
#
#     433  {target, ms}                 -- a target to scroll and a dwell
#     175  {target}                     -- just a target
#      70  {dy}                         -- the amount, no dx
#      12  {target, direction, amount}  -- prose form
#       5  {target, direction, ms}
#       1  {target, delta_y}
#
# This is the `key_sequence`/`keys` defect (v0.5) in a second verb: the model
# authors a perfectly clear intent, one handler subscript it did not spell turns
# it into a KeyError, and the step is lost with no evidence collected. Found while
# testing whether `xXbgiFY` item 12's "scrolling changes the viewer range" half
# could pass -- it cannot even be attempted, because the scroll never dispatches.
#
# Repaired here rather than in `_action_scroll` for the same reason as the other
# args: `browser_actions` is shared with the web UI verify service, where a step
# with a genuinely absent argument should still raise.
#
# `ms` is deliberately NOT read as an amount. On a scroll step it is a dwell (the
# same key `wait` uses for time), and treating 1200 as 1200px of wheel would
# invent a scroll distance the plan never asked for.
_SCROLL_AMOUNT_KEYS = ("delta_y", "deltaY", "amount", "distance", "pixels", "px")
_SCROLL_DX_KEYS = ("delta_x", "deltaX")
# What a bare `scroll` means with no amount named: one comfortable screenful down.
# Matches the fallback default already in `executor.execute_scroll`.
_SCROLL_DEFAULT_DY = 400


def _num(val: Any) -> float | None:
    """`val` as a number, or None. Accepts the numeric strings models emit."""
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val.strip())
        except ValueError:
            return None
    return None


def repair_scroll_args(action: dict[str, Any]) -> dict[str, Any]:
    """Give a `scroll` step the `dx`/`dy` its handler subscripts (DEFECT 48).

    The amount comes from `dy`, else a synonym, else the default screenful; the
    SIGN and AXIS come from `direction` when the model wrote one, so "scroll up"
    is a negative dy rather than the downward default. A step that already
    carries both keys is returned unchanged, so a well-formed plan is unaffected.
    """
    if action.get("action") != "scroll":
        return action
    has_dx, has_dy = _num(action.get("dx")) is not None, _num(action.get("dy")) is not None
    if has_dx and has_dy:
        return action
    direction = str(action.get("direction") or "").strip().lower()
    horizontal = direction in ("left", "right")
    # Magnitude: whatever the model named, on either axis, else the default.
    mag = _num(action.get("dy")) if has_dy else None
    if mag is None:
        mag = _num(action.get("dx")) if (horizontal and has_dx) else None
    if mag is None:
        for k in (_SCROLL_DX_KEYS if horizontal else ()) + _SCROLL_AMOUNT_KEYS:
            mag = _num(action.get(k))
            if mag is not None:
                break
    if mag is None:
        mag = float(_SCROLL_DEFAULT_DY)
    mag = abs(mag)
    # Sign from the stated direction. An unstated direction keeps the sign the
    # model wrote (a negative dy already means "up"), defaulting to down.
    if direction in ("up", "left"):
        signed = -mag
    elif direction in ("down", "right"):
        signed = mag
    else:
        prior = _num(action.get("dy")) if has_dy else _num(action.get("dx"))
        signed = mag if prior is None or prior >= 0 else -mag
    dx, dy = (signed, 0.0) if horizontal else (0.0, signed)
    out = {k: v for k, v in action.items()
           if k not in _SCROLL_AMOUNT_KEYS and k not in _SCROLL_DX_KEYS}
    out["dx"], out["dy"] = int(dx), int(dy)
    out["note"] = ((out.get("note") or "")
                   + f" [scroll args: dx={int(dx)} dy={int(dy)}]").strip()
    return out


def repair_action_args(action: dict[str, Any]) -> dict[str, Any]:
    """Flatten an argument envelope, then rename a misnamed argument.

    Returns the action unchanged when its arguments are already canonical, when
    the verb has no known synonyms, or when no synonym is present either -- an
    absent argument stays absent, so the handler still raises. `scroll` is the
    one verb whose missing arguments are FILLED rather than left absent (see
    :func:`repair_scroll_args`): its handler needs two subscripts the model
    almost never writes, and the intent is unambiguous without them.
    """
    for env in _ARG_ENVELOPES:
        inner = action.get(env)
        if isinstance(inner, dict) and inner:
            merged = {k: v for k, v in inner.items() if k not in action}
            if merged:
                action = {**{k: v for k, v in action.items() if k != env}, **merged,
                          "note": ((action.get("note") or "")
                                   + f" [flattened {env}]").strip()}
            break
    # Runs BEFORE the synonym table: `scroll` is not in it, so an early return
    # below would skip the repair entirely.
    if action.get("action") == "scroll":
        return repair_scroll_args(action)
    spec = _ARG_SYNONYMS.get(action.get("action"))
    if not spec:
        return action
    canon, alts = spec
    if action.get(canon) is not None:
        return action
    for alt in alts:
        val = action.get(alt)
        if val is None:
            continue
        # `keys` is a LIST for key_sequence; a singular `key` must be wrapped or
        # the handler iterates the string's characters ("shift" -> s,h,i,f,t,
        # which is five wrong keypresses rather than one right one).
        if canon == "keys" and not isinstance(val, list):
            val = [val]
        # ...and the reverse: `press` takes ONE key, so a list must be unwrapped.
        if canon == "key" and isinstance(val, list):
            if not val:
                continue
            val = val[0]
        out = {k: v for k, v in action.items() if k != alt}
        out[canon] = val
        out["note"] = ((out.get("note") or "") + f" [arg {alt}->{canon}]").strip()
        return out
    return action


def sanitize_actions(raw: list[Any]) -> list[dict[str, Any]]:
    """Keep only well-formed actions whose verb is in the allowed vocabulary.

    Also normalizes any `for_item` stamp to its bare id form so a model-labeled
    ``"item 4"`` joins the same scorer `pair_map` bucket as the back-filled
    ``"4"`` (see :func:`normalize_item_id`), repairs handler arguments the model
    spelled with a synonym (see :func:`repair_action_args`), and collapses the
    impossible click-combobox-then-click-option pair into a single runnable
    ``select`` (see :func:`collapse_select_pairs`)."""
    out: list[dict[str, Any]] = []
    for a in raw or []:
        if isinstance(a, dict) and a.get("action") in ALLOWED_ACTIONS:
            if a.get("for_item") is not None:
                a = {**a, "for_item": normalize_item_id(a.get("for_item"))}
            a = repair_action_args(a)
            # DEFECT 97: a `goto` with no destination cannot do anything, and
            # unlike a missing selector there is no salvage tier to fall back on.
            # Dropping it here keeps it out of the step budget entirely; leaving
            # it in would spend a step to record a self-inflicted error.
            if a.get("action") == "goto" and not str(a.get("url") or "").strip():
                continue
            out.append(a)
    return collapse_select_pairs(out)


# `combobox 'Lock viewport to device'` / `option 'iPhone 15 Pro · 393'` — the AX
# phrasing the generator writes for a native dropdown. Matched BEFORE selector
# normalization, while the role word is still intact.
_AX_ROLE_RE = re.compile(r"""^\s*(?P<role>[a-z]+)\s+(?P<q>["'])(?P<txt>.+?)(?P=q)\s*$""")


def _ax_role(target: Any) -> tuple[str, str] | None:
    m = _AX_ROLE_RE.match(str(target or ""))
    return (m.group("role").lower(), m.group("txt").strip()) if m else None


def collapse_select_pairs(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rewrite `click combobox 'L'` + `click option 'V'` into one `select`.

    A native `<select>` cannot be driven by clicking: the option list is painted
    by the OS, so Playwright refuses to click an `<option>`, and `page.fill`
    rejects a `<select>` outright. The generator nonetheless authors the
    two-click form a human would perform, because that IS what a human does. Left
    alone both steps dead-wait their full timeout and error — and then the item's
    own captured action runs against the DEFAULT option and produces a real
    before/after pair showing the wrong value, which the judge scores a
    determinate False. `4ewUptv` went 1.000 -> 0.000 that way.

    Collapsing preserves the intent exactly: one `select` carrying the combobox
    as target and the option label as value. The option's LABEL is what the
    generator writes, and `_action_select` tries label before value before index.

    A lone `click option 'V'` with no preceding combobox is also rewritten, to a
    `select` whose target is the select CONTAINING that option — the generator
    sometimes emits only the option step. A lone combobox click is left alone: it
    is a legitimate focus action and there is no value to choose.
    """
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(actions):
        a = actions[i]
        ra = _ax_role(a.get("target")) if a.get("action") == "click" else None
        nxt = actions[i + 1] if i + 1 < len(actions) else None
        rn = _ax_role(nxt.get("target")) if (nxt and nxt.get("action") == "click") else None
        if ra and ra[0] == "combobox" and rn and rn[0] == "option":
            # Carry the PAIR's capture/for_item forward. The authored capture
            # usually sits on the second step (choosing is the state change), so
            # take whichever step declared one rather than assuming a position.
            merged: dict[str, Any] = {
                "action": "select",
                "target": f"combobox '{ra[1]}'",
                "value": rn[1],
                "note": ((a.get("note") or nxt.get("note") or "")
                         + " [collapsed combobox+option into select]").strip(),
            }
            for key in ("for_item", "capture"):
                val = nxt.get(key) if nxt.get(key) is not None else a.get(key)
                if val is not None:
                    merged[key] = val
            out.append(merged)
            i += 2
            continue
        if ra and ra[0] == "option":
            merged = {**a, "action": "select",
                      "target": f"select:has(option:text-is(\"{ra[1]}\"))",
                      "value": ra[1],
                      "note": ((a.get("note") or "")
                               + " [option click rewritten to select]").strip()}
            merged.pop("text", None)
            out.append(merged)
            i += 1
            continue
        out.append(a)
        i += 1
    return out


# Forward-only launch/advance labels seen on real game setup funnels. ONLY verbs
# that move DEEPER toward gameplay — never BACK/CANCEL/MENU — so a speculative
# click can't retreat out of the funnel. Substring, case-insensitive (Playwright
# :has-text semantics), so "START" also covers "START RACE"/"START GAME".
_GAME_KEYS = frozenset({
    "w", "a", "s", "d", "arrowup", "arrowdown", "arrowleft", "arrowright",
    " ", "space", "spacebar",
})

# Unambiguous game-launch verbs. A plan whose FIRST meaningful click carries one
# of these (e.g. `button:has-text("ENGAGE")`) is launching a game even when it
# never presses a movement key afterwards — some plans just click Start then
# `conclude`, trusting the landing screen to become gameplay. Deliberately
# game-flavored and NARROW: it excludes ENTER / GO / NEXT / CONTINUE / CONFIRM /
# SUBMIT / OK, which are common on forms and dashboards, so the launch-click
# fallback in ``inject_funnel_advance`` never fires a speculative `advance` on a
# non-game plan. A false miss here is cheap (the plain click still runs); a false
# hit on a form is not, so we err toward missing.
# DEFECT 82 -- "GARAGE" was here, and it is not a launch verb. On the live
# claude-opus-5 run, `task_0001` (a race game) planned `click GARAGE` as its
# FIRST action, for item 10 ("Garage opens and shows content"). GARAGE matched,
# so `launch_idx` was 0, the advance battery was prepended at position 0, and
# `advance` -- correctly scoring the live setup screen -- pressed START RACE.
# The race began before ANY of the plan's eight menu-scoped clicks ran; all
# eight then timed out at 2500ms against a menu that no longer existed, 10 of 12
# items went indeterminate, and item 2 ("Forward key moves car forward") was
# graded a determinate FALSE on frames of a rolled car in a split-screen race
# the verifier itself had started. Critical item, so the gate floored a working
# artifact to 0.000.
#
# GARAGE is a nav destination on a setup screen at least as often as it is a
# forward step in a funnel, and it never needs to be an ANCHOR: `advance` scores
# the live DOM, so a genuine "TO THE GARAGE ->" funnel step is still found and
# clicked once the battery is anchored somewhere. This list only answers "is
# this plan launching a game", and GARAGE cannot establish that on its own --
# exactly the false hit the docstring above warns is not cheap.
_LAUNCH_VERBS = (
    "START", "PLAY", "BEGIN", "LAUNCH", "ENGAGE", "DEPLOY", "RACE", "FIGHT",
    "DROP IN", "NEW GAME", "GET STARTED", "PLAY GAME",
)


def _is_launch_click(action: dict[str, Any]) -> bool:
    """True if this is a click whose target names a game-launch verb.

    Games frequently gate gameplay behind a single START/ENGAGE control the
    planner clicks by ``:has-text(...)``; that plain coordinate click can be
    swallowed by a full-screen canvas or an overlay stacked over the button (see
    ``browser_actions._action_advance``), leaving the game on its title screen —
    a verifier false-negative. Recognizing the launch click lets us anchor the
    DOM-aware ``advance`` battery (which has an occlusion-proof native-dispatch
    fallback) just before it, so the game reliably starts even when the plan
    presses no movement keys. Matched on the target's uppercased text against the
    narrow ``_LAUNCH_VERBS`` set so form/dashboard clicks stay untouched."""
    if action.get("action") != "click":
        return False
    tgt = str(action.get("target", "")).upper()
    return any(v in tgt for v in _LAUNCH_VERBS)


def _advance_action() -> dict[str, Any]:
    """One DOM-aware 'advance' step: click the on-screen forward CTA.

    Emits the executor's ``advance`` verb, which scores the LIVE visible controls
    and clicks the real 'go deeper' button whatever its flavor-text label
    ("TO THE GARAGE ->", "ENTER THE CIRCUIT", "DROP IN"). This succeeds where a
    CSS ":has-text" union failed: the union returns the first DOM match (usually
    a content card whose text merely contains a launch word like "Race again")
    and the funnel stalls, whereas the scorer rejects reverse/content controls
    and targets the primary forward button. A screen with no forward control
    (already in gameplay) makes it a cheap no-op."""
    return {"action": "advance",
            "note": "auto: advance through setup funnel to reach gameplay"}


def _is_game_drive(action: dict[str, Any]) -> bool:
    """True if this action drives gameplay with the keyboard (see _GAME_KEYS)."""
    verb = action.get("action")
    if verb == "key_sequence":
        return True  # a sequence of keys is gameplay driving, not a lone menu key
    if verb == "press":
        return str(action.get("key", "")).strip().lower() in _GAME_KEYS
    return False


def inject_funnel_advance(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Insert speculative launch/advance clicks before the first gameplay key.

    Games commonly hide the real content behind a MULTI-STEP setup funnel (e.g.
    a 'STEP 01/02' config wizard whose 'TO THE GARAGE ->' launch button only
    appears on a screen the single-pass planner never saw). Without this, the
    planner clicks the first Start then fires movement keys on the config screen
    and the run scores a blank menu — the single biggest game-scoring bug found.

    Gated on the plan being a game — either it drives with the keyboard
    (``_is_game_drive``) OR its first meaningful click is a game-launch control
    (``_is_launch_click``). Non-game plans (dashboards, forms) are returned
    untouched. The launch-click anchor matters for plans that click START/ENGAGE
    then `conclude` without ever pressing a key: those still need the DOM-aware
    `advance` (with its occlusion-proof native-dispatch fallback) so a launch
    button hidden under a canvas/overlay actually fires. Idempotent: does nothing
    if an auto-advance click is already present."""
    if any(a.get("note", "").startswith("auto: advance") for a in actions):
        return actions
    # Anchor the advance battery at the EARLIER of (a) the first gameplay key or
    # (b) the first game-launch click. Inserting before a launch click means the
    # robust `advance` gets the game past its title screen first; the planner's
    # own click then runs too (harmless — a re-click of a now-hidden button is a
    # ~0ms selector miss). Using the earliest anchor keeps the advance steps in
    # front of the first thing that needs a live gameplay screen.
    drive_idx = next((i for i, a in enumerate(actions) if _is_game_drive(a)), None)
    launch_idx = next((i for i, a in enumerate(actions) if _is_launch_click(a)), None)
    drive_idx_key = drive_idx  # remembered: the keypress anchor, if there was one
    candidates = [i for i in (drive_idx, launch_idx) if i is not None]
    if not candidates:
        return actions  # neither a keyboard-driven game nor a launch-click game
    drive_idx = min(candidates)
    # DEFECT 82b -- if the anchor is a launch CLICK that some checklist item owns,
    # put the battery AFTER it, not before. `advance` presses the same control the
    # item's own click was going to press, so anchoring in front makes the item's
    # "before" frame a picture of gameplay already in progress and its own click a
    # selector miss against a screen that is gone -- the item degrades to
    # indeterminate for a reason that is purely our ordering. Running the item's
    # click first keeps its before/after pair honest; the battery immediately
    # after still crosses the rest of the funnel, and still supplies the
    # occlusion-proof native dispatch if that plain click was swallowed (which is
    # the whole reason the launch anchor exists). A gameplay-KEY anchor keeps the
    # original placement: nothing owns a keypress the way an item owns its click.
    after_anchor = (drive_idx == launch_idx
                    and drive_idx != drive_idx_key
                    and str(actions[drive_idx].get("for_item") or "").strip() != "")
    # Three advance steps, each followed by a settle wait: enough to cross the
    # deepest funnels seen (title -> RACE PROTOCOL -> GARAGE -> gameplay is 3
    # forward clicks). Each advance is a no-op once gameplay is reached, so an
    # extra one past the end costs a ~0ms scan, not a wrong click.
    settle = {"action": "wait", "ms": 700, "note": "auto: let screen settle"}
    battery: list[dict[str, Any]] = []
    for _ in range(3):
        battery.append(_advance_action())
        battery.append(dict(settle))
    at = drive_idx + 1 if after_anchor else drive_idx
    return actions[:at] + battery + actions[at:]


def _authored_targets(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Per item id, the authored action_plan steps that name a resolvable target."""
    out: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        cid = normalize_item_id(str(it.get("check_item_id", "")))
        for step in it.get("action_plan") or []:
            if isinstance(step, dict) and step.get("target"):
                out.setdefault(cid, []).append(step)
    return out


def retarget_blind_probes(actions: list[dict[str, Any]],
                          items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace invented coordinates with the target the checklist actually named.

    Two independent defects, both measured on b0811, produce the same wrong
    outcome — an interaction dispatched at a pixel that belongs to a full-screen
    canvas rather than to the widget under test:

    1. The plan model overwhelmingly prefers `drag {from:[x,y],to:[x,y]}` over a
       named target (1,254 blind vs 268 targeted) EVEN WHEN the item's own
       `action_plan` authored a perfectly resolvable one. `slider 'Symmetry'` is
       handled correctly by `executor.normalize_selector`; the planner simply
       discarded it. Where the item authored a target for the same verb, restore
       it and drop the coordinates so `_resolve_coord_action` measures the live
       element instead.
    2. Whether or not an authored target exists, coordinates outside the
       1280x720 driving viewport cannot be what the model meant (see
       `DRIVE_VP`). With no target to fall back on, the step is dropped: an
       interaction we cannot aim is an absence of evidence, which degrades the
       item to indeterminate and never gates — dispatching it anyway
       manufactures a confident-looking AX diff for the wrong widget, which is
       the false negative this whole pass exists to remove.
    """
    authored = _authored_targets(items)
    out: list[dict[str, Any]] = []
    for a in actions:
        verb = a.get("action")
        if verb not in ("drag", "scroll", "mouse_move") or a.get("target"):
            out.append(a)
            continue
        pts = [p for p in (a.get("from"), a.get("to")) if p is not None]
        if verb == "mouse_move" and isinstance(a.get("x"), (int, float)):
            pts.append([a.get("x"), a.get("y")])
        if not pts:
            out.append(a)
            continue
        cid = normalize_item_id(a.get("for_item"))
        # Prefer a target the checklist author wrote for this very item, matching
        # the verb first so a drag does not inherit a button's selector.
        cands = authored.get(cid) or []
        pick = next((s for s in cands if s.get("action") == verb), None) or \
            next((s for s in cands if s.get("action") in ("drag", "click", "press",
                                                          "key_sequence")), None)
        if pick is not None:
            repl = {k: v for k, v in a.items() if k not in ("from", "to", "x", "y")}
            repl["target"] = pick["target"]
            repl["note"] = ((a.get("note") or "")
                            + " [auto: blind coords -> authored target]")
            out.append(repl)
            continue
        if all(_in_viewport(p) for p in pts):
            out.append(a)
            continue
        # Out of viewport and nothing to aim at. Drop it (see docstring).
        continue
    return out


# DEFECT 66. Verbs that cannot serve as a capture point. `wait` observes without
# acting, so a pair around it measures the passage of time rather than the effect
# of an interaction; `conclude` terminates the executor loop, so anything after
# the first one never runs (b0811 `zgFoa46`: 12 authored `conclude` probes, 11
# items judged with zero steps executed, one of them gating the task to 0.0).
# Everything else in ALLOWED_ACTIONS performs something a before/after pair can
# measure.
# `goto` joins them for a different reason than the other two: a before/after
# pair around a navigation always shows a changed page, so it "passes" for every
# item indiscriminately -- evidence-shaped noise rather than evidence.
_NON_PROBE_VERBS = {"wait", "conclude", "goto"}


def _is_probeable(step: dict[str, Any]) -> bool:
    """True if a before/after pair around `step` would measure an interaction."""
    verb = step.get("action")
    return bool(verb) and verb in ALLOWED_ACTIONS and verb not in _NON_PROBE_VERBS


def _capture_steps(it: dict[str, Any], covered: set[str]) -> list[dict[str, Any]]:
    """The steps of `it`'s action_plan that should carry a before/after capture.

    Authored markers win outright: if the generator marked any step, those are
    returned untouched and nothing is inferred. This keeps the author in control
    of multi-probe items (an item may legitimately want a pair around two
    different steps) and keeps this function a no-op on every checklist that was
    already well-formed.

    DEFECT 66: when nobody marked anything — neither the generator here nor the
    planner model, whose stamps arrive via `covered` — infer ONE capture point on
    the LAST probeable step. Last, because a plan reads as setup followed by the
    interaction the check is about ("click the SIMULATION tab, then drag VISCOSITY"
    for an item about the viscosity readout); a pair around the first step would
    measure the tab switch and answer a question nobody asked.

    Returns [] for an item that already has a planner-model capture point, and []
    for an item whose plan is inert (only wait/conclude). The inert case is
    deliberate: see defect 65 — manufacturing a probe there would invent an
    interaction the author never wrote, while leaving it indeterminate merely
    means it cannot gate.
    """
    plan = [s for s in (it.get("action_plan") or []) if isinstance(s, dict)]
    authored = [s for s in plan if s.get("capture") == "before_after"]
    if authored:
        return authored
    if normalize_item_id(str(it.get("check_item_id", ""))) in covered:
        return []
    probeable = [s for s in plan if _is_probeable(s)]
    if not probeable:
        return []
    # Tagged distinctly from an authored probe. `_compare_items` reads these notes
    # to attribute a score change, and if an inferred capture point were spelled
    # the same as an authored one, "execution CHANGED" would be unattributable
    # between a checklist the generator marked and one this pass inferred.
    return [{**probeable[-1], "capture": "before_after",
             "note": ((probeable[-1].get("note") or "")
                      + " [auto: inferred capture point]")}]


def _ensure_directional_steps(actions: list[dict[str, Any]],
                              items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Guarantee every before_after step from the items' action_plans is present
    in the trajectory, stamped with its for_item. The VLM planner may drop or
    fail to tag a directional step; the scorer needs a captured before/after
    pair per directional item, so we deterministically append any missing ones
    just before `conclude`.

    Idempotent on TWO keys, because one is not enough:

    * `(for_item, action, key, target)` — the exact step. `action` is part of the
      key: without it an authored `wait` (target None) and an emitted blind
      `drag` (target None) for the same item collide, and the authored probe —
      the one that actually performs the interaction — is silently suppressed.
      That hit 678 items on b0811, 78 of them critical and judged False.
    * `(for_item, action, label)` where the label is extracted from whatever
      selector dialect each step used (see `target_label`). Exact-key dedup
      finds zero duplicates here because `button 'DRIFT'` and
      `button:has-text("DRIFT")` are different strings; the semantic key finds
      395 of 5,088 probes (7.8%), all clicks — a back-filled re-click of a
      toggle the model already clicked, which flips the feature back off between
      the before and after frames.

    DEFECT 66. Everything above is conditional on SOMEONE having written
    `capture: "before_after"`, and two independent parties may: the checklist
    generator (on an `action_plan` step, which this function then guarantees
    reaches the trajectory) or the planner model (on one of its own actions, as
    the prompt asks). Both are models, and when neither obliges, the item reaches
    the judge with no pair at all. The judge then abstains — "requires screenshot
    evidence" — which never gates, so instead of failing loudly the item quietly
    leaves the determinate denominator. That is how `mKoh6BY` reported **1.000 on
    8 of 12 determinate items**: a perfect score standing on two thirds of the
    checklist.

    Measured on the 10-task acceptance set: 44 of 120 items (36.7%) had a
    generator marker, 96 (80.0%) had a planner-model marker, and 13 actionable
    items (12.1% of actionable) had NEITHER — 9 of them critical, so each was one
    coin flip away from zeroing its whole task on evidence nobody collected.

    So the marker is now inferred rather than required: an item whose authored
    plan contains a runnable interaction gets a capture point on its LAST such
    step, because that is the step whose effect the check describes (a plan
    clicks a tab, then drags the slider whose readout the item is about). The
    preference order matters and is deliberate — first adopt a matching step the
    planner model ALREADY placed in the trajectory (stamping costs nothing and
    keeps the interaction where the model sequenced it), and only append a new
    probe when there is nothing to adopt. Appending blindly would re-click a
    toggle the model already clicked, which is the exact regression the semantic
    dedup key above exists to prevent.

    Items with an INERT authored plan (nothing but `wait`/`conclude`) get
    nothing. They are defect 65's ambient checks: a manufactured probe would
    invent an interaction the author never asked for, and an ambient item that
    stays indeterminate cannot gate, which is the safer of the two failures.
    """
    def keys(a: dict[str, Any], cid: Any) -> tuple[tuple, tuple]:
        tgt = a.get("target")
        return ((cid, a.get("action"), a.get("key"), tgt),
                (cid, a.get("action"), target_label(tgt)))

    have: set[tuple] = set()
    covered: set[str] = set()   # items that already have a capture point somewhere
    for a in actions:
        if a.get("capture") != "before_after":
            continue
        cid_a = normalize_item_id(a.get("for_item"))
        exact, sem = keys(a, cid_a)
        have.add(exact)
        have.add(sem)
        if cid_a:
            covered.add(cid_a)

    # DEFECT 66, pass 1: adopt in place. For an item with no capture point from
    # either model, if the trajectory ALREADY contains an action that matches one
    # of its authored interactions, stamp that action instead of appending a
    # second one. The model sequenced its plan for a reason (open the panel, then
    # move the control), and a duplicate probe appended at the end re-fires a
    # toggle — the regression the semantic dedup key was added to stop.
    actions = list(actions)
    for it in items:
        cid = normalize_item_id(str(it.get("check_item_id", "")))
        if not cid or cid in covered:
            continue
        wanted = [s for s in (it.get("action_plan") or [])
                  if isinstance(s, dict) and _is_probeable(s)]
        if not wanted:
            continue
        # Last authored interaction first: the check describes the effect of the
        # final step of its plan, not of the setup that preceded it.
        hit = None
        for step in reversed(wanted):
            lbl = target_label(step.get("target"))
            for idx, a in enumerate(actions):
                if a.get("capture") == "before_after":
                    continue          # already someone else's probe
                if a.get("action") != step.get("action"):
                    continue
                if lbl is None or target_label(a.get("target")) != lbl:
                    continue
                # Only adopt an untagged action or one already ours: stealing
                # another item's stamped step would move its pair.
                if normalize_item_id(a.get("for_item")) not in (None, "", cid):
                    continue
                hit = idx
                break
            if hit is not None:
                break
        if hit is None:
            continue
        a = actions[hit]
        actions[hit] = {**a, "for_item": cid, "capture": "before_after",
                        "note": ((a.get("note") or "")
                                 + " [auto: adopted as capture point]")}
        exact, sem = keys(actions[hit], cid)
        have.add(exact)
        have.add(sem)
        covered.add(cid)

    missing: list[dict[str, Any]] = []
    for it in items:
        cid = normalize_item_id(str(it.get("check_item_id", "")))
        # DEFECT 66, pass 2: when nothing could be adopted, append a probe built
        # from the item's own last authored interaction. `_capture_steps` returns
        # the authored capture steps when there are any and otherwise infers one,
        # so an item the generator DID mark keeps exactly the steps it asked for.
        for step in _capture_steps(it, covered):
            if not isinstance(step, dict):
                continue
            # The authored step must pass the SAME action vocabulary the model's
            # steps pass — this back-fill runs AFTER `sanitize_actions`, so an
            # unrunnable verb ("screenshot", "keydown", "navigate", ...) would
            # otherwise reach the executor, error out, and produce a fake pair.
            # Skipping leaves the item with no evidence => indeterminate, which
            # never gates. `.get` (not `[...]`) because nothing validates step
            # shape upstream — a step with no "action" must skip, not KeyError.
            if step.get("action") not in ALLOWED_ACTIONS:
                continue
            # `conclude` TERMINATES the executor loop, so it can never probe
            # anything: whatever is appended after the first one never runs. On
            # b0811 `zgFoa46` the generator authored `conclude` +
            # capture:before_after for all 12 items, this back-fill emitted 12
            # probe steps, the executor stopped at the first, and the other 11
            # items were judged with ZERO steps executed — item 7 took a
            # determinate False and gated the artifact to 0.0 on an interaction
            # that was never attempted. An item with no runnable probe is
            # indeterminate, which never gates.
            if step.get("action") == "conclude":
                continue
            exact, sem = keys(step, cid)
            if exact in have or sem in have:
                continue
            emitted = {**step, "for_item": cid,
                       "note": (step.get("note") or "") + " [auto: directional probe]"}
            # This back-fill runs AFTER `sanitize_actions`, so an argument the
            # generator spelled with a synonym has not been repaired yet: 45 of
            # the 51 dead `key_sequence` probes in 400 b0811 tasks were authored
            # here, carrying a singular `key`. Repair before emitting, for the
            # same reason the ALLOWED_ACTIONS check above is repeated here.
            emitted = repair_action_args(emitted)
            # Normalize ms to hold_ms for press/key_sequence actions
            if emitted.get("action") in ("press", "key_sequence") and "ms" in emitted and "hold_ms" not in emitted:
                emitted["hold_ms"] = emitted.pop("ms")
            missing.append(emitted)
            have.add(exact)
            have.add(sem)
    if not missing:
        return actions
    # insert before a trailing conclude if present, else append
    if actions and actions[-1].get("action") == "conclude":
        return actions[:-1] + missing + [actions[-1]]
    return actions + missing


# DEFECT 46 (consecutive clicks on one canvas all landing on the same pixel) is
# fixed in the EXECUTOR, not here — see `_canvas_repeat_position` in executor.py.
# It is worth recording why, because the obvious place for it is this file.
#
# `page.click("canvas")` aims at the element's CENTRE, every time. A vertex-based
# drawing tool needs distinct points, so a plan like
#
#   click canvas  "Click first point of polygon."
#   click canvas  "Click second point of polygon."
#   click canvas  "Click third point of polygon to close/create the body."
#
# places one vertex three times. Each step reports `result: "ok"` — the click WAS
# delivered — and no polygon is ever created, which is the b0811 `mKoh6BY` report:
#
#   "The execution agent failed to create a polygon."
#
# and its sibling item 3 ("erase tool removes drawn bodies"), which cannot pass
# because there is nothing to erase. Both were critical items scoring 0.000.
#
# Deciding it here would mean deciding by SPELLING: the planner has never touched
# the page, so all it can test is whether the selector string looks canvas-ish.
# That misses `#c`, `#sim`, `#myCanvas > *` and every other id an artifact happens
# to choose, and it would also offset clicks on a plain div that merely happens to
# be called `#board`. The executor knows the live tag, so it makes the call there.


def _links_text(links: Any) -> str:
    """The artifact's page inventory as a prompt block (empty when single-page).

    Rendered as `url<TAB>link text` so the model can match a checklist item that
    names the DESTINATION ("the Fluid Paint demo") to the path that reaches it,
    which is the join it was previously performing by guesswork.
    """
    if not isinstance(links, list) or not links:
        return ""
    rows = "\n".join(
        f"  {str(l.get('url'))[:120]}\t{str(l.get('text') or '')[:60]}"
        for l in links if isinstance(l, dict) and l.get("url"))
    if not rows:
        return ""
    return ("OTHER PAGES OF THIS ARTIFACT (use goto{url} to reach one, and "
            "goto{url} back to the entry page before any item about the landing "
            "page):\n" + rows + "\n\n")


# Prompt budget for the control inventory. Chosen against the DOM snapshot's own
# 6000-char cap (see `plan()`): the inventory is a SUMMARY of the same page, so it
# must not outweigh the primary evidence. Measured need on the eval25 sample: 10 of
# 12 renderable artifacts fit in under 1700 chars, and the two that did not were a
# 7-page admin dashboard (500 controls, 13763 chars) and its sibling (425, 12320) —
# i.e. the cap only binds on data-grid pages, where the 240th `button "Edit"` row
# teaches the planner nothing the deduplicated count already told it.
INV_CHAR_BUDGET = 4500
# Every page keeps at least this many lines even when the budget is exhausted.
# Without a floor a single grid-heavy page consumes the whole allowance and the
# later pages render empty — which is exactly the failure this module exists to
# fix, arrived at from the other direction: the planner would again be told the
# artifact's other pages have no controls on them.
INV_MIN_LINES_PER_PAGE = 5


def _fair_share(page_lines: list[list[str]],
                budget: int = INV_CHAR_BUDGET,
                floor: int = INV_MIN_LINES_PER_PAGE) -> list[int]:
    """How many lines each page may print: a per-page floor, then round-robin.

    Water-filling rather than "truncate the concatenation", because the pages are
    not interchangeable. A plan can only reach page 7 by `goto`, so dropping page
    7 entirely to finish printing page 1's data grid removes the only information
    that could have produced a reachable target there.

    Bounded by construction: the floors are charged against the budget before any
    round-robin, so the result is at most `budget` plus one line, or — when the
    floors alone exceed the budget — the cost of the floors (at most
    `len(page_lines) * floor * 121` chars).
    """
    n = len(page_lines)
    if n <= 0:
        return []
    take = [min(len(ls), max(0, floor)) for ls in page_lines]
    used = sum(len(ls[j]) + 1 for ls, k in zip(page_lines, take) for j in range(k))
    progress = True
    while used < budget and progress:
        progress = False
        for i, ls in enumerate(page_lines):
            if take[i] >= len(ls):
                continue
            cost = len(ls[take[i]]) + 1
            if used + cost > budget:
                continue
            take[i] += 1
            used += cost
            progress = True
    return take


def _inventory_text(inv: Any) -> str:
    """The per-page control inventory as a prompt block (empty when unavailable).

    Grouped by page rather than flattened, because "which page is this control
    on" is the difference between an aimable target and a `selector not found`:
    the plan starts on the entry page and can only reach the rest through
    `goto`. An empty or failed inventory renders to "", which reproduces exactly
    the pre-inventory prompt — that is the fail-open path, and it is why a broken
    capture costs context and never costs a task.
    """
    pages = (inv or {}).get("pages") if isinstance(inv, dict) else None
    if not isinstance(pages, list):
        return ""
    kept: list[tuple[str, list[str], int]] = []   # (head, lines, total_distinct)
    for pg in pages:
        if not isinstance(pg, dict):
            continue
        els = [f"    {str(e)[:120]}" for e in (pg.get("elements") or []) if str(e).strip()]
        if not els:
            continue
        head = f"  [{str(pg.get('label') or 'page')[:40]}] {str(pg.get('url') or '')[:120]}"
        try:
            total = max(int(pg.get("total") or 0), len(els))
        except (TypeError, ValueError):
            total = len(els)
        kept.append((head, els, total))
    if not kept:
        return ""

    take = _fair_share([ls for _, ls, _ in kept])
    chunks, dropped = [], 0
    for (head, els, total), n in zip(kept, take):
        body = "\n".join(els[:n])
        withheld = total - n
        if withheld > 0:
            dropped += withheld
            body += f"\n    ... [{withheld} more distinct controls on this page]"
        chunks.append(head + "\n" + body)
    tail = ""
    if dropped:
        # Never a silent cap: a block that quietly stops listing reads to the
        # model as "that is all there is", and it will then plan as though the
        # page were fully described. Saying how many were withheld keeps the
        # speculative-follow-on instruction in _SYS honest.
        tail = (f"  ({dropped} further controls exist but are not listed here — "
                f"the page may hold controls beyond those above.)\n")
    return ("INTERACTIVE ELEMENTS PRESENT (captured from the RENDERED pages; "
            "role \"accessible name\" state):\n" + "\n".join(chunks) + "\n" + tail + "\n")


def _reground(actions: list[dict[str, Any]], messages: list[dict[str, Any]],
              items: list[dict[str, Any]], initial_obs: dict[str, Any],
              vlm_call: Callable, workspace: Any,
              max_retries: int = 1) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """DEFECT 103. Re-ask the planner when it planned against a product it remembers.

    The planner is shown one screenshot and 6 KB of the landing page's DOM. When
    the build task names something well known, that gap gets filled from memory:
    on `eval25_o5` the Habitica plan clicked `Add Task`, `Inventory`, `Shops`,
    `Party`, `Challenges` and `Settings` -- the real product's nav, absent from
    an artifact that is a landing page. 27% of all failed steps in that run named
    text that exists nowhere in the artifact, and each one pushed its checklist
    item to INDETERMINATE, which is strictly worse than a failed check: the
    artifact gets no credit for what it does do.

    So we check the plan against the artifact's whole source tree -- every page
    and component, which is more than the planner was ever shown -- and if it
    invented a meaningful share of its targets, we say so BY NAME and hand back
    the artifact's real label vocabulary.

    Fail open at every step: any exception, an unreadable workspace, or a corpus
    too small to prove absence leaves the original plan untouched. Returns the
    chosen actions and a report that is always safe to write to plan.json.
    """
    hist: list[dict[str, Any]] = []
    try:
        corpus = plan_grounding.build_corpus(workspace)
        authored = plan_grounding.authored_labels(items)
        rep = plan_grounding.check_plan(actions, corpus, authored)
        hist.append({k: rep[k] for k in
                     ("checked", "grounded", "ungrounded", "planner_invented",
                      "invented_frac", "should_regenerate")})
        if not rep.get("should_regenerate"):
            rep["attempts"] = 1
            rep["history"] = hist
            return actions, rep
        best, best_rep = actions, rep
        convo = list(messages)
        for _ in range(max(0, int(max_retries))):
            convo = convo + [
                {"role": "assistant",
                 "content": json.dumps({"actions": best}, ensure_ascii=False)[:60000]},
                {"role": "user",
                 "content": plan_grounding.feedback_message(best_rep, corpus, initial_obs)},
            ]
            obj = vlm_call(convo)
            retry = sanitize_actions(obj.get("actions") if isinstance(obj, dict) else None)
            if not retry:
                break        # a broken retry must never replace a working plan
            r2 = plan_grounding.check_plan(retry, corpus, authored)
            hist.append({k: r2[k] for k in
                         ("checked", "grounded", "ungrounded", "planner_invented",
                          "invented_frac", "should_regenerate")})
            # Keep the retry only if it actually grounded more of itself AND did
            # not shrink the plan -- a model can always "fix" ungrounded targets
            # by deleting the steps that had them, which measures less and scores
            # worse. Fewer invented targets, no fewer probes, or we keep the
            # original.
            better = (r2["planner_invented"] < best_rep["planner_invented"]
                      and len(retry) >= 0.8 * len(best))
            if better:
                best, best_rep = retry, r2
            if not r2.get("should_regenerate"):
                break
        best_rep["attempts"] = len(hist)
        best_rep["history"] = hist
        best_rep["regenerated"] = best is not actions
        return best, best_rep
    except Exception as exc:  # noqa: BLE001 — a grounding check must never fail a task
        return actions, {"error": f"{type(exc).__name__}: {exc}"[:300],
                         "attempts": len(hist) or 1, "history": hist}


def _benchmark_coverage_enabled() -> bool:
    """True when the driver exported ``IV_BENCH_COVERAGE`` non-falsy.

    Gates `_synthesize_bridge_probes`. Matches the ``IV_GROUND_RETRIES`` /
    ``IV_INVENTORY_PAGES`` env-flag pattern used elsewhere in this module —
    the planner has no config object, and `iv_verify.main()` exports run.*
    knobs before importing the planner.
    """
    v = (os.environ.get("IV_BENCH_COVERAGE") or "").strip().lower()
    return v not in ("", "0", "false", "no", "off")


# Compact benchmark-mode keyboard battery for canvas games. Arrow keys and WASD
# (movement), Space (jump/attack/select), Enter (confirm). Short holds so a
# single before/after pair captures both input effect and the return-to-idle
# frame. See `_synthesize_bridge_probes`.
_BENCH_GAME_BATTERY: tuple[str, ...] = (
    "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
    "w", "a", "s", "d", " ", "Enter",
)

# Textual cues that mark an item as canvas/keyboard driven — the class of
# check that has no AX-visible control and can only be probed via keyboard.
# Deliberately narrow so a form/dashboard item that happens to say "select"
# doesn't get a keyboard battery it doesn't need.
_BENCH_KEYBOARD_CUES = (
    "arrow", "wasd", "w/a/s/d", "keyboard", "keys", "keypress", "press ",
    "spacebar", "space bar", " space", "rhythm", "combo", "hold ",
    "move", "movement", "walk", "run", "jump", "attack", "shoot",
    "player", "character", "controls", "battle", "combat", "canvas",
)


def _text_hints_keyboard(item: dict[str, Any]) -> bool:
    """Does the item's own text look canvas/keyboard-driven?"""
    fields = (item.get("checking_content") or "", item.get("expected_result") or "",
              item.get("check_item_id") or "")
    text = " ".join(str(f) for f in fields).lower()
    return any(cue in text for cue in _BENCH_KEYBOARD_CUES)


def _synthesize_bridge_probes(actions: list[dict[str, Any]],
                              items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Benchmark-mode: emit a measurable probe for items with no `action_plan`.

    v2-bridge checklists (`origin: "v2-bridge"`) carry no `action_plan`, so
    `_ensure_directional_steps` — which only adopts/back-fills from an existing
    action_plan — has nothing to work with, and every item degrades to
    INDETERMINATE. For canvas+keyboard games (task_0114 character-select,
    task_0090 dungeon crawler) this is a total coverage gap: the artifact
    RENDERS and can be probed, we just never bind an item to a step.

    This pass runs AFTER `_ensure_directional_steps` (so it never overrides
    what that pass already did) and, for each still-uncovered item with no
    `action_plan`, appends ONE ``key_sequence`` step stamped
    ``for_item`` + ``capture:"before_after"``. It ALSO appends a matching
    ``{"capture":"before_after", ...}`` marker to ``item["action_plan"]`` so
    that ``scorer._is_directional(item)`` becomes True — the items list flows
    into scoring by reference, and this is how a generator-authored capture
    would make the item directional.

    Discriminating, not lenient: only items whose text hints at keyboard/canvas
    input get a battery. An item with no cue is left INDETERMINATE (which the
    reconciler will exclude+retry). Never manufactures an interaction the item
    doesn't imply.

    Off by default; gated on ``IV_BENCH_COVERAGE`` via
    ``_benchmark_coverage_enabled()``.
    """
    # Two-key dedup, same shape as _ensure_directional_steps L1033-1036.
    def keys_of(a: dict[str, Any], cid: Any) -> tuple[tuple, tuple]:
        tgt = a.get("target")
        return ((cid, a.get("action"), a.get("key"), tgt),
                (cid, a.get("action"), target_label(tgt)))

    have: set[tuple] = set()
    covered: set[str] = set()
    for a in actions:
        if a.get("capture") != "before_after":
            continue
        cid_a = normalize_item_id(a.get("for_item"))
        exact, sem = keys_of(a, cid_a)
        have.add(exact)
        have.add(sem)
        if cid_a:
            covered.add(cid_a)

    appended: list[dict[str, Any]] = []
    for it in items:
        cid = normalize_item_id(str(it.get("check_item_id", "")))
        if not cid or cid in covered:
            continue
        # Only synthesize when there is genuinely NO action_plan. An item whose
        # plan is inert (wait/conclude only) is defect-65 ambient — leave it
        # INDETERMINATE rather than manufacture an interaction the author
        # didn't ask for.
        if it.get("action_plan"):
            continue
        if not _text_hints_keyboard(it):
            continue
        step = {"action": "key_sequence",
                "keys": list(_BENCH_GAME_BATTERY),
                "hold_ms": 120, "delay_ms": 40,
                "for_item": cid, "capture": "before_after",
                "note": "[auto: bench_coverage bridge probe]"}
        step = repair_action_args(step)
        if step.get("action") not in ALLOWED_ACTIONS:
            continue
        exact, sem = keys_of(step, cid)
        if exact in have or sem in have:
            continue
        appended.append(step)
        have.add(exact)
        have.add(sem)
        covered.add(cid)
        # Make the item directional in-place, so `scorer._is_directional`
        # (which reads item["action_plan"]) picks up the trajectory pair.
        ap = it.get("action_plan")
        if not isinstance(ap, list):
            ap = []
        ap.append({"action": "key_sequence", "capture": "before_after",
                   "note": "[auto: bench_coverage marker]"})
        it["action_plan"] = ap

    if not appended:
        return actions
    if actions and actions[-1].get("action") == "conclude":
        return actions[:-1] + appended + [actions[-1]]
    return actions + appended


def plan(task_prompt: str, initial_obs: dict[str, Any], items: list[dict[str, Any]],
         vlm_call: Callable, *, workspace: Any = None,
         ground_retries: int = 1) -> dict[str, Any]:
    text = (
        f"BUILD TASK:\n{task_prompt}\n\n"
        f"CHECKLIST (interaction scripts):\n{_checklist_text(items)}\n\n"
        f"{_links_text(initial_obs.get('links'))}"
        # Tier-2 observation: role + accessible name for every control on every
        # page, so the plan can be aimed at what was built rather than at what a
        # similar product has. Before the DOM snapshot because the snapshot is
        # the part that gets truncated at 6 KB.
        f"{_inventory_text(initial_obs.get('inventory'))}"
        f"INITIAL DOM SNAPSHOT:\n{(initial_obs.get('dom_snapshot') or '')[:6000]}"
    )
    blocks: list[dict[str, Any]] = [{"type": "text", "text": text}]
    shot = initial_obs.get("screenshot")
    if shot and os.path.exists(shot):
        blk = image_block(shot)
        if blk is not None:
            blocks.append(blk)
    messages = [
        {"role": "system", "content": _SYS},
        {"role": "user", "content": blocks},
    ]
    obj = vlm_call(messages)
    # Drop anything the model returned that isn't a known verb before it reaches
    # the executor. An empty plan is a hard failure (nothing to exercise) — raise
    # so the pipeline records a "plan" error verdict rather than scoring a page
    # that was never interacted with.
    actions = sanitize_actions(obj.get("actions") if isinstance(obj, dict) else None)
    if not actions:
        raise PlanError("planner returned no usable actions")
    # DEFECT 103: before anything else touches the plan, check that the targets
    # it names exist in the artifact at all, and re-ask with the real label
    # vocabulary if the model planned against a product it remembers. Done here,
    # on the raw model output, so the report attributes invented targets to the
    # planner and not to the back-fill passes below (which import targets from
    # the checklist and would muddy the count).
    actions, grounding = _reground(actions, messages, items, initial_obs,
                                   vlm_call, workspace, ground_retries)
    # Deterministically walk multi-step setup funnels before the first gameplay
    # key (no-op for non-games). The prompt asks the model to do this, but a
    # single-pass planner can't see the wizard screens that only appear after the
    # first click, so we guarantee it here rather than hope the model guessed.
    actions = inject_funnel_advance(actions)
    # Prefer the checklist's own named targets over coordinates the model
    # invented for a page it imagined taller than the 1280x720 we drive.
    actions = retarget_blind_probes(actions, items)
    actions = _ensure_directional_steps(actions, items)
    # Benchmark-mode: for v2-bridge checklists with no `action_plan`, synthesize
    # ONE keyboard-battery probe per uncovered keyboard-cued item, so canvas
    # games (task_0114 rhythm/character-select, task_0090 dungeon crawler)
    # aren't scored all-INDETERMINATE. Off by default; distillation behaviour
    # unchanged. See `_synthesize_bridge_probes`.
    if _benchmark_coverage_enabled():
        actions = _synthesize_bridge_probes(actions, items)
    # LAST, so it also stamps the probes the step above just back-filled: a drag
    # with no coordinates is swept left-to-right (to maximum) unless it says
    # otherwise, which silently made every "reaches 0 / goes negative" item
    # impossible to satisfy. See `stamp_drag_directions`.
    actions = stamp_drag_directions(actions, items)
    return {"actions": actions, "grounding": grounding}
