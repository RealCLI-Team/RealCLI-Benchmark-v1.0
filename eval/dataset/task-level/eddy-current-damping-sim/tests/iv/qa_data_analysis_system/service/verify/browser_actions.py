"""Pure browser action executor — dispatches actions against a page-like object."""


def _timeout_kw(action):
    """Playwright timeout kwargs from an optional ``timeout`` (ms) in the action.

    Selector-based actions (click/hover/type) auto-wait for the element and, left
    unbounded, block for Playwright's 30s default when the selector never matches
    — the dominant executor cost on artifacts whose live DOM differs from the
    planner's guessed selectors. Callers cap it by injecting ``timeout``; callers
    that don't (e.g. the web UI verify service) keep the default behavior."""
    t = action.get("timeout")
    return {"timeout": t} if isinstance(t, (int, float)) else {}


def _action_kw(action):
    """Timeout kwargs plus an optional ``force`` bypass of actionability checks.

    ``force=True`` makes Playwright skip the visible/stable/enabled/hit-test gates
    and click the element directly. This is load-bearing for GAME MENUS: they
    animate continuously (a looping background or pulsing CTA), so the element's
    bounding box never stops moving and Playwright's *stability* check never
    settles — every click then dead-waits the full timeout and fails, so the game
    never launches and scores a blank menu regardless of the artifact's quality.
    A real user can click an animating button fine; ``force`` lets us too. Only
    the interactive verifier sets it (as a fallback retry after a normal click
    times out); the web UI verify service never does, keeping its behavior."""
    kw = _timeout_kw(action)
    if action.get("force"):
        kw["force"] = True
    return kw


def _action_click(page, action):
    """Click the target element, optionally at an offset inside its box.

    ``position`` is `{"x": px, "y": px}` relative to the element's top-left, and
    it is what makes a CANVAS clickable more than once. Without it every
    `click canvas` lands on the same centre pixel, so a three-click polygon
    places one vertex three times (DEFECT 46; see `spread_canvas_clicks` in the
    interactive verifier, which is the only caller that sets it).
    """
    target = action["target"]
    kw = _action_kw(action)
    pos = action.get("position")
    if isinstance(pos, dict) and "x" in pos and "y" in pos:
        kw["position"] = {"x": pos["x"], "y": pos["y"]}
    page.click(target, **kw)
    return {"result": "ok"}


def _action_hover(page, action):
    """Hover over the target element."""
    target = action["target"]
    page.hover(target, **_action_kw(action))
    return {"result": "ok"}


def _action_type(page, action):
    """Fill target element with text."""
    target = action["target"]
    text = action["text"]
    page.fill(target, text, **_action_kw(action))
    return {"result": "ok"}


def _action_select(page, action):
    """Choose an option in a native ``<select>``.

    A native select is the one common control the click/type vocabulary CANNOT
    drive, and the failure was silent and expensive. Playwright refuses to click
    an `<option>` (the list is rendered by the OS, not the page) and `page.fill`
    rejects a `<select>` outright, so a plan that asked to "choose iPhone 15 Pro"
    got translated into `click select…` + `type` — two actions that each
    dead-waited ~15s and then errored. The item's OWN captured action ran next
    and succeeded against the DEFAULT option, so the judge received a genuine
    before/after pair showing the wrong value and returned a determinate False.
    On a critical item that gates the whole artifact: `4ewUptv` went 1.000 ->
    0.000 for locking the viewport to its default 1280px instead of the 393px
    the never-selected option would have given. Measured on b0811/full: 154 of
    5,321 tasks (2.9%) carry a dropdown step, 33 of them on a critical item.

    ``value`` is matched by LABEL first, then by value, then as an index. The
    generator writes what a human reads on screen ("iPhone 15 Pro · 393"), which
    is the option's label; its `value` attribute is usually an internal token
    ("393" or "iphone15pro"). Trying label first and falling back means an
    authored human-readable string works without the generator knowing the DOM.
    """
    target = action["target"]
    raw = action.get("value", action.get("text"))
    if raw is None:
        return {"error": "select requires a `value` (option label, value, or index)"}
    value = str(raw)
    kw = _timeout_kw(action)
    # select_option raises when nothing matches, so each strategy is tried in
    # turn and only an all-miss is an error. Returning the strategy that worked
    # keeps the trace honest about HOW the option was resolved.
    for how, arg in (("label", {"label": value}),
                     ("value", {"value": value}),
                     ("index", {"index": int(value)} if value.lstrip("-").isdigit() else None)):
        if arg is None:
            continue
        try:
            page.select_option(target, **arg, **kw)
        except Exception:  # noqa: BLE001 - try the next matching strategy
            continue
        return {"result": "ok", "selected": value, "select_by": how}
    return {"error": f"no option matching {value!r} in {target}"}


def _action_press(page, action):
    """Press a key.

    Optional ``hold_ms``: hold the key down for that long before releasing
    (keydown → wait → keyup) instead of an instant tap. Games read *held* keys
    to accelerate/steer, so a tap often produces no visible motion; a hold does.
    Omitted (the web UI verify service never sets it) → the original instant
    press, unchanged."""
    key = action["key"]
    hold_ms = action.get("hold_ms")
    if isinstance(hold_ms, (int, float)) and hold_ms > 0:
        page.keyboard.down(key)
        page.wait_for_timeout(hold_ms)
        page.keyboard.up(key)
    else:
        page.keyboard.press(key)
    return {"result": "ok"}


def _action_scroll(page, action):
    """Scroll by dx, dy using mouse wheel."""
    dx = action["dx"]
    dy = action["dy"]
    page.mouse.wheel(dx, dy)
    return {"result": "ok"}


def _action_wait(page, action):
    """Wait for milliseconds."""
    ms = action["ms"]
    page.wait_for_timeout(ms)
    return {"result": "ok"}


def _action_mouse_move(page, action):
    """Move mouse to x, y."""
    x = action["x"]
    y = action["y"]
    page.mouse.move(x, y)
    return {"result": "ok"}


# DEFECT 55: a drag dispatched as ONE pointer move draws nothing on a canvas.
#
# The reviewer's `mKoh6BY` remark was "fail to create objects on canvas". The
# artifact is fine: driving its FREEHAND tool by hand with a multi-point stroke
# produces `MODEL IMMERSED` and `LATTICE CELLS`. What the harness sent was a
# two-point path — down, one `mousemove`, up — so a tool that builds its shape
# from sampled `pointermove` events received a single sample and had no polyline
# to close.
#
# Swept on the extracted artifact, same from/to, varying only the sample count:
#
#     moves   MODEL IMMERSED
#       1     False     <- what this handler sent
#       2     True
#       4..16 True
#
# The threshold is exactly at the boundary, which is why this reads as "canvas
# drags don't work" rather than as an obviously-wrong path. Interpolating the
# EXISTING straight segment is enough — an enclosing loop was measured too and
# is not required — so this is a sampling fix, not a new gesture.
#
# 24 is Playwright's own idiom for a human-like drag, well past the threshold
# and still one round trip. Verified not to disturb the case that dominates the
# corpus: a range input dragged to its far edge lands on 100 at steps=1 and at
# steps=24 alike, because a slider tracks the FINAL position and does not care
# how many samples got there.
_DRAG_STEPS = 24


def _action_drag(page, action):
    """Drag from one coordinate to another, sampling the path (see DEFECT 55)."""
    from_coords = action["from"]
    to_coords = action["to"]
    page.mouse.move(from_coords[0], from_coords[1])
    page.mouse.down()
    page.mouse.move(to_coords[0], to_coords[1], steps=_DRAG_STEPS)
    page.mouse.up()
    return {"result": "ok"}


def _action_key_sequence(page, action):
    """Press a sequence of keys.

    Optional ``hold_ms``: hold EACH key for that long (down → wait → up) rather
    than tapping — the difference between "car twitches" and "car actually drives"
    for canvas games. Optional ``delay_ms``: pause between keys. Both omitted
    (web UI verify service) → the original instant taps."""
    keys = action["keys"]
    hold_ms = action.get("hold_ms")
    delay_ms = action.get("delay_ms")
    hold = isinstance(hold_ms, (int, float)) and hold_ms > 0
    for key in keys:
        if hold:
            page.keyboard.down(key)
            page.wait_for_timeout(hold_ms)
            page.keyboard.up(key)
        else:
            page.keyboard.press(key)
        if isinstance(delay_ms, (int, float)) and delay_ms > 0:
            page.wait_for_timeout(delay_ms)
    return {"result": "ok"}


def _action_conclude(page, action):
    """Signal conclusion."""
    return {"result": "conclude"}


# In-page scorer for the "advance" action. Runs in the browser (page.evaluate)
# so it sees the LIVE screen — the only way to reach a forward control on a
# wizard screen the single-pass planner never saw. Returns the best forward-CTA
# element's center coordinates + label, or null if none is on screen.
#
# Why a scorer and not a CSS ":has-text" union: game funnels label their launch
# button with flavor text ("TO THE GARAGE ->", "ENTER THE CIRCUIT", "DROP IN")
# and pack the screen with content buttons (circuit cards, "GROW", difficulty
# chips) whose text also contains launch words ("Race again", "Restart"). A
# keyword union returns the FIRST DOM match — usually a content card — and the
# funnel never advances. Scoring by forward-CTA *signals* (a launch verb as a
# short standalone label, a -> / play glyph, a primary-button class, position at
# the bottom/right where "next" lives) and explicitly rejecting reverse controls
# (BACK/CANCEL/CLOSE/MENU) picks the real "go deeper" button instead.
# Marks the chosen element with a data attribute so Python can click it via a
# stable selector (Playwright auto-scrolls it into view). "CSS-visible" here is
# deliberately NOT "in the viewport": the launch button often sits BELOW the
# fold (e.g. "TO THE GARAGE ->" at y=1189 in a 720px viewport) — excluding it by
# viewport bounds was why an early version no-op'd on exactly the screen it
# needed to advance. We require only that layout/visibility/opacity make it
# clickable; scrolling is the clicker's job.
_ADVANCE_MARK = "data-iv-advance"

# Ceiling on each JS evaluation inside `advance`. See `_bounded_eval`.
ADVANCE_EVAL_TIMEOUT_MS = 3000

_ADVANCE_JS = r"""() => {
  const FWD = ['START','BEGIN','PLAY','GO','LAUNCH','DEPLOY','ENTER','GARAGE',
    'CONTINUE','NEXT','PROCEED','CONFIRM','READY','ENGAGE','DROP IN',"LET'S GO",
    'RACE','FIGHT','GET STARTED','NEW GAME','RESUME'];
  const REV = ['BACK','CANCEL','CLOSE','EXIT','MENU','QUIT','SETTINGS','OPTIONS',
    'CONTROLS','HELP','MUTE','PAUSE','RESTART','REPLAY','←','⟵','< '];
  // Match a keyword at WORD boundaries, not as a raw substring. This is
  // load-bearing: a substring test made 'PLAY' fire inside 'NETPLAY (SIM)' — a
  // network-mode toggle on a race-config screen — so `advance` clicked the
  // toggle instead of the real DEPLOY launch button and mutated the config
  // (regressed one game 0.865 -> 0.365). Word boundaries also stop 'GO' from
  // matching 'GOAL'/'GOOGLE', 'ENTER' from 'CENTER', etc. Alphanumeric runs are
  // the word chars; arrow/punctuation keys ('←','< ') have no letters, so for
  // those we fall back to a plain substring test.
  const hasKw = (up, k) => {
    const kt = k.trim();
    if (!/[A-Z0-9]/.test(kt)) return up.includes(kt);   // arrow/glyph key
    const lit = kt.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return new RegExp('(^|[^A-Z0-9])' + lit + '($|[^A-Z0-9])').test(up);
  };
  const vh = window.innerHeight, doc = document.documentElement;
  const pageH = Math.max(doc.scrollHeight, vh);
  // NOTE (DEFECT 43): this scorer deliberately does NOT hit-test. Every signal
  // here is computed style — `visibility`, `display`, `opacity`, box area — so a
  // CTA underneath an overlay scores exactly as well as a reachable one, and on
  // b0811 `QAmsEw4` that lost the task: `advance` picked the console's "▶ PLAY"
  // behind `#gate` three times, each reporting ok with
  // `ax_diff: "(no accessibility-tree change)"`.
  //
  // Rejecting occluded candidates was tried and REVERTED. It regressed the
  // `_smoke_advance` occluded-fallback case (an ENGAGE button under a decorative
  // `#nofx` veil, the real 7YE7ZVR bug): that overlay is not dismissible, but
  // `_action_advance`'s native-dispatch fallback clicks straight through it and
  // the game does reach gameplay. A hit-test turns "reachable by dispatch" into
  // "no candidate at all" — strictly worse, and it trades a common rescue for a
  // rarer one.
  //
  // The gate case is fixed at its root instead: `dismiss_entry_gate` in
  // executor.py lifts a screen-covering entry gate right after `settle()`, before
  // any evidence is taken, so by the time this scorer runs the gate is gone and
  // the reachable CTA is the only one on screen.
  document.querySelectorAll('[data-iv-advance]').forEach(e => e.removeAttribute('data-iv-advance'));
  const els = document.querySelectorAll('button,a,[role="button"],input[type="submit"],input[type="button"]');
  let best = null, bestScore = 0;
  els.forEach(e => {
    const r = e.getBoundingClientRect();
    const cs = getComputedStyle(e);
    // CSS-visible & laid out — NOT required to be within the viewport (the
    // clicker scrolls to it). A zero-box or hidden element is genuinely off.
    const visible = r.width > 0 && r.height > 0 &&
      cs.visibility !== 'hidden' && cs.display !== 'none' && Number(cs.opacity) > 0.1;
    if (!visible) return;
    // A NAV TAB is never the wizard-forward button. Games put the launch CTA
    // ("DEPLOY", big, at the bottom) alongside a same-word nav TAB ("DEPLOY"
    // as a top toolbar tab that just switches panels). Clicking the tab does
    // nothing and the game never launches. role="tab", an aria-selected attr,
    // or a "tab" class all mark a tab — reject them outright so the real CTA
    // wins. (This zeroed the SCRAPYARD arena game: advance kept clicking the
    // already-active DEPLOY *tab* instead of the DEPLOY launch *button*.)
    const role = (e.getAttribute('role') || '').toLowerCase();
    const cls = (e.className || '') + ' ' + (e.id || '');
    const isTab = role === 'tab' || e.hasAttribute('aria-selected') ||
      /\btab\b/i.test(cls);
    if (isTab) return;
    const txt = ((e.innerText || e.value || '') + '').trim();
    const up = txt.toUpperCase();
    if (REV.some(k => hasKw(up, k))) return;         // never a reverse control
    let score = 0;
    if (FWD.some(k => hasKw(up, k))) score += 5;     // carries a forward verb
    if (/[→▶►➜➤]|->/.test(txt)) score += 3;          // forward arrow / play glyph
    if (up && up.length <= 24) score += 2;           // a CTA, not a paragraph
    else if (up.length > 60) score -= 3;             // long text = content, not CTA
    // Class/id names of real launch CTAs. Broadened beyond the earlier list so
    // a `btn-deploy`/`enter`/`engage`/`begin` primary button out-scores a bare
    // same-word control (the arena's launch button is `btn btn-deploy`).
    if (/\b(primary|cta|start|play|launch|go|next|confirm|submit|race|deploy|enter|engage|begin|drop|fight|hero)\b/i.test(cls)) score += 3;
    // A launch CTA is PROMINENT — large area. A nav link/toggle is small. Use
    // the element's box area (px) as a soft prominence signal so the big
    // bottom "DEPLOY" beats a small toolbar "DEPLOY" even when both carry the
    // verb. Thresholds are generous (a real CTA is easily >12k px^2).
    const area = r.width * r.height;
    if (area > 12000) score += 2;
    else if (area < 2000) score -= 1;                // tiny = chrome, not a CTA
    // Forward CTAs sit toward the bottom of the page (using absolute document
    // position, so a below-the-fold "next" still reads as "low").
    const absTop = r.top + window.scrollY;
    if (absTop > pageH * 0.6) score += 1;
    if (r.left + window.scrollX > doc.scrollWidth * 0.5) score += 1;
    if (score > bestScore) { bestScore = score; best = e; }
  });
  if (!best || bestScore < 5) return null;           // require a real forward verb
  best.setAttribute('data-iv-advance', '1');
  return { label: ((best.innerText || best.value || '') + '').trim().slice(0, 40),
           score: bestScore };
}"""


def _bounded_eval(page, js, *, timeout_ms=ADVANCE_EVAL_TIMEOUT_MS, default=None):
    """``page.evaluate(js)``, but bounded — returns ``default`` on timeout.

    ``page.evaluate()`` accepts NO timeout argument, and ``set_default_timeout``
    does not apply to it (measured: 19.7s on a page whose main thread was pegged
    for 20s, with the default set to 8s). JS cannot run until the renderer's main
    thread yields, so on an artifact doing continuous rAF/WebGL work every
    evaluation dead-waits for the animation loop.

    That is the measured exec-time tail of the b0811 sweep. `advance` evaluates
    ``_ADVANCE_JS`` twice plus a marker cleanup, and five tasks spent 1141-1405s
    inside a SINGLE `advance` step (`error=None`, no console output); frame mtimes
    on `…audio-re__MGGcEis` pin 854s between `000_initial.png` and
    `001_advance.png`. Nothing above can catch it: the executor's EXEC_BUDGET_S is
    checked only BETWEEN steps, so one blocked step overruns the whole budget.

    ``Locator.evaluate`` DOES take a timeout and it is honoured against a pegged
    main thread — verified against the installed API: 2.00s to raise, versus
    22.70s for the equivalent `page.evaluate` on the same wedged page. So route
    through a locator on ``:root``, which every HTML document has, and pass the
    JS unchanged: the callable receives that element as its argument, and none of
    the JS here uses its argument (all three reach for `document` directly).

    A timeout returns ``default`` rather than raising, because a saturated
    renderer is not an action failure: `advance` degrades to its ordinary
    no-op/skip paths, which is the same thing it does on a screen with no forward
    control. `axtree.capture` bounds the same class of hazard by capping how MANY
    times it pays an unboundable call; here the call itself is boundable, so this
    caps each one directly."""
    try:
        return page.locator(":root").evaluate(js, timeout=timeout_ms)
    except Exception:
        # Fake/stub pages (smoke tests, the web UI's page-likes) have no
        # `locator`; fall back to the plain evaluate so behaviour is unchanged
        # for them. A real page that raises here timed out or navigated away.
        if not hasattr(page, "locator"):
            try:
                return page.evaluate(js)
            except Exception:
                return default
        return default


def _action_advance(page, action):
    """Click the 'go deeper' control to walk a multi-step game setup funnel.

    Scores the LIVE visible controls (see ``_ADVANCE_JS``), marks the best
    forward CTA with a data attribute, then clicks it by that stable selector so
    Playwright scrolls it into view first — the launch button is frequently below
    the fold. Returns ``{"result": "noop"}`` (not an error) when no forward
    control scores high enough — a screen with none, or the gameplay canvas — so
    a speculative advance is a cheap no-op rather than a failure. Only the
    interactive verifier emits this verb; the web UI verify service never does.

    Every JS evaluation here goes through ``_bounded_eval``: on a renderer pegged
    by continuous rAF/WebGL work an unbounded one blocks for as long as the
    animation loop runs, which cost 1141-1405s in a single `advance` step on the
    b0811 sweep."""
    hit = _bounded_eval(page, _ADVANCE_JS)
    if not hit:
        # Either no forward control scored high enough, or the renderer was too
        # busy to answer within the cap. Both are a cheap no-op, never an error.
        return {"result": "noop"}
    sel = f"[{_ADVANCE_MARK}]"
    label = hit.get("label", "")
    method = "force-click"
    try:
        # Click the already-scored-and-marked CTA with force=True directly. Game
        # menus animate continuously (looping background, pulsing button), so
        # Playwright's *stability* actionability gate never settles and a normal
        # click times out — even though the pointer events would fire and launch
        # the game. Worse, launching hides the menu, so a post-click recheck then
        # reports "element not visible", turning a SUCCESSFUL launch into an
        # error. We've already decided this is the forward CTA, so bypass the
        # gates: force dispatches the click immediately and reliably. (This is
        # what turned the SCRAPYARD arena game from a stuck 0.0 into a launched,
        # scored run.)
        page.click(sel, timeout=2500, force=True)
    except Exception:
        # A missing element / navigation mid-click surfaces here; the effect
        # check below decides whether a fallback is warranted, so swallow it.
        pass

    # Detect an INEFFECTIVE coordinate click and fall back to a native DOM click.
    # ``force=True`` skips the actionability *gates* (visible/stable/enabled) but
    # NOT hit-testing: Playwright still dispatches the pointer event at the
    # element's center coordinate, so anything painted on top of the CTA there —
    # a full-screen gameplay <canvas>, or a stacked overlay div (e.g. a
    # position:fixed "NO WEBGL" sheet that is present in the layout even while
    # `hidden`) — receives the click instead, and the button's own handler never
    # fires. The game stays on its title screen and scores a blank menu: a
    # verifier false-negative, not a broken build. If the SAME forward CTA is
    # still on screen after the click, it didn't advance — so dispatch a native
    # DOM 'click' event on the marked element, which invokes its click handler
    # directly and ignores whatever is painted over it. (This rescued
    # INTERSTELLAR GLIDE / 7YE7ZVR, whose ENGAGE button was covered by a hidden
    # #nofx overlay: coordinate force-click hit the overlay, native dispatch
    # launched the game.) Re-evaluating _ADVANCE_JS re-marks the current best CTA
    # under the same selector, so the dispatch targets whatever is live now.
    still = _bounded_eval(page, _ADVANCE_JS)
    if still and still.get("label", "") == label:
        try:
            page.dispatch_event(sel, "click", timeout=2500)
            method = "native-dispatch"
        except Exception:
            pass

    # Drop the marker so the next advance re-scores from scratch (the click may
    # have navigated away, in which case this is a harmless no-op). Bounded like
    # the scoring evaluations: a stale marker only costs the next advance a
    # re-score, whereas an unbounded cleanup costs the step its whole budget.
    _bounded_eval(page, "() => document.querySelectorAll('[%s]')"
                        ".forEach(e => e.removeAttribute('%s'))"
                        % (_ADVANCE_MARK, _ADVANCE_MARK))
    return {"result": "ok", "advanced_to": label, "advance_method": method}


ACTIONS = {
    "click": _action_click,
    "hover": _action_hover,
    "type": _action_type,
    "select": _action_select,
    "press": _action_press,
    "scroll": _action_scroll,
    "wait": _action_wait,
    "mouse_move": _action_mouse_move,
    "drag": _action_drag,
    "key_sequence": _action_key_sequence,
    "advance": _action_advance,
    "conclude": _action_conclude,
}


def execute(page, action):
    """Execute a single action against a page-like object.

    Args:
        page: A Playwright page-like object with methods like click, hover, etc.
        action: A dictionary with "action" key and action-specific parameters.

    Returns:
        A dictionary with either {"result": "ok"/"conclude"} or {"error": str}.
    """
    action_name = action.get("action")

    if action_name not in ACTIONS:
        return {"error": f"unknown action: {action_name}"}

    handler = ACTIONS[action_name]

    try:
        return handler(page, action)
    except Exception as e:
        return {"error": str(e)}
