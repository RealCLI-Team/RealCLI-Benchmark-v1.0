"""Check an action plan's target strings against the artifact's own source.

Defect 103. The planner sees one screenshot and the first 6 KB of the landing
page's DOM. When the build task names a well-known product, the model fills that
gap from memory: on ``eval25_o5`` the Habitica plan spent seven steps clicking
``Add Task``, ``Inventory``, ``Shops``, ``Party``, ``Challenges``, ``Settings``
-- the real product's navigation. The artifact that was actually built is a
landing page. None of those strings exist anywhere in it, all seven steps failed
``selector not found``, and every item that depended on them went indeterminate.

Across that run 27% of failed steps named text that appears nowhere in the
artifact. That is not a selector bug and no amount of selector repair reaches
it: the plan is about a different program.

The fix is to ground the plan before it runs. We already have the artifact's
whole source tree on disk, which is strictly more than the planner was shown --
it contains every sub-page, every component, and every label the artifact can
ever display, not just the ones the landing page happened to mount. So: extract
the text literals the plan intends to click, look for them in that corpus, and
if the planner invented a meaningful number of them, tell it so -- by name --
and let it plan again with the artifact's real label vocabulary in hand.

Three invariants, each of them a lesson this repo has already paid for:

**Fail open.** No corpus, an unreadable workspace, a scan that throws -- keep the
original plan and record why. A grounding check that eats plans when it cannot
read the disk would zero tasks in the one direction nobody audits.

**Never delete a step.** Ungrounded is evidence, not proof: text can be built at
runtime (i18n, template concatenation, ``textContent`` assignment), and a click
that matches nothing is skipped in ~0 ms. We warn and re-ask; we do not prune.
The plan that runs is always a plan some model actually authored.

**Only judge what the planner chose.** Checklist-authored targets are copied
verbatim on purpose (the ``for_item`` contract), so an ungrounded authored target
is the checklist generator's business, not the planner's. Counting it toward the
retry trigger would spend a model call on something regeneration cannot fix.
"""
from __future__ import annotations

import os
import re
from typing import Any, Iterable

# Everything the artifact could carry a visible label in. Wider than
# `loader._CODE_SUFFIXES` (html/css/js) on purpose: a Vue/Svelte/JSX artifact
# keeps every one of its labels in files that list does not read, which would
# make the whole corpus look empty and every target look invented.
SRC_SUFFIXES = frozenset({
    ".html", ".htm", ".xhtml", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx",
    ".vue", ".svelte", ".astro", ".css", ".scss", ".sass", ".less", ".json",
    ".md", ".mdx", ".txt", ".svg", ".hbs", ".ejs", ".pug", ".php", ".liquid",
    # Data and template dialects. A static-site generator keeps its whole nav in
    # `_data/navigation.yml`; omitting .yml made the checker report the real
    # heading "Installation" as invented, which is the one error class that
    # matters here -- crying wolf spends a planner call and can replace a good
    # plan with a worse one. Every suffix added moves toward GROUNDED.
    ".yml", ".yaml", ".toml", ".xml", ".njk", ".mustache", ".erb", ".twig",
    ".jinja", ".j2", ".csv", ".tsv", ".py",
})
SKIP_DIRS = frozenset({
    "node_modules", ".git", ".cache", "__pycache__", ".venv", "venv",
    ".next", ".nuxt", ".svelte-kit", ".parcel-cache", ".pytest_cache",
    ".turbo", "coverage", ".vercel",
})
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_CORPUS_BYTES = 24 * 1024 * 1024
# Below this the corpus is not credible evidence of absence (an empty or
# unreadable tree), so the check abstains rather than declaring everything
# invented. One <div> of boilerplate clears it; a missing workspace does not.
MIN_CORPUS_BYTES = 200

# Retry trigger. Both must hold: enough ungrounded targets to be a pattern
# rather than one runtime-built string, and a large enough share of the plan
# that re-planning is worth a model call.
# Thresholds calibrated on the 25-trial eval25_o5 replay (20 plans with a
# fetched artifact tree). The distribution is bimodal, which is what makes a
# threshold possible at all: one plan was 63% invented (a whole remembered
# auth flow -- Login / Register / Forgot Password / Back to Login), a second
# 31%, a third 6 steps at 18%, and the long tail is 0-1 stray literals. Firing
# needs a large FRACTION of the plan invented, or a large ABSOLUTE number of
# steps; either alone is the signature, and a lone miss never is.
MIN_UNGROUNDED = 3
MIN_UNGROUNDED_FRAC = 0.20
MIN_UNGROUNDED_ABS = 6
MIN_LITERAL_LEN = 3
# Above this a "literal" is not a control label but extraction noise -- the
# replay produced `JavaScriptThe language that lets `[] + {}` happen. Still my
# favourite.` from a bare-prose target. Absence of a paragraph from the source
# proves nothing about the plan, so long literals are not judged.
MAX_LITERAL_LEN = 60

_QUOTED = re.compile(r"""["']([^"']{2,80})["']""")
_ATTR_TEXT = re.compile(
    r"""\[(?:aria-label|title|placeholder|alt|name|data-testid)\s*[*^$~|]?=\s*["']([^"']+)["']\]""",
    re.IGNORECASE)
_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^0-9a-z]+")
# Text-bearing verbs. A `goto{url}` or `scroll{dx,dy}` names no label, and a
# `press{key}` names a keyboard key, not page text.
_TEXT_VERBS = frozenset({"click", "hover", "type", "select", "drag", "advance"})


def _norm(s: str) -> str:
    return _WS.sub(" ", str(s).replace(" ", " ")).strip().lower()


def _squeeze(s: str) -> str:
    """Alphanumerics only. Used ONLY against the harvested label inventory.

    The same label is spelled `Add Task`, `Add&nbsp;Task`, `add-task` and
    `Add\\n  Task` across HTML, JSX and Vue. Squeezing makes those one string.

    It is deliberately NOT used against raw source text. Measured on the
    Habitica trial: the plan's invented `"Add Task"` squeezes to `addtask`,
    which matched the JS identifier `addTask(` -- the one and only occurrence in
    73 files. An identifier is not a rendered label, so the loose form has to be
    confined to text we already believe is a label.
    """
    return _NON_ALNUM.sub("", str(s).lower())


_WORD = re.compile(r"[a-z0-9]+")
_GAP = r"(?:<[^>]{0,120}>|&[a-z#0-9]{2,8};|[^a-z0-9]){1,12}"
_MAX_TOKENS = 12


def _literal_re(literal: str) -> "re.Pattern[str] | None":
    """A pattern matching ``literal`` as *rendered text*, not as an identifier.

    Words must be separated by at least one non-alphanumeric character, and the
    match must not run into a surrounding word. So `Add</span> Task`,
    `add-task`, `"Add",\n"Task"` all ground `Add Task`, while `addTask` and
    `inventoryItems` do not. Returns None for a literal with no word content.
    """
    toks = _WORD.findall(_norm(literal))[:_MAX_TOKENS]
    if not toks:
        return None
    # A gap is punctuation/whitespace/entities, or a whole tag -- `Add</span> Task`
    # is one label. What it can never be is nothing at all, which is what makes
    # `addTask` fail to ground `Add Task`.
    body = _GAP.join(re.escape(t) for t in toks)
    try:
        return re.compile(r"(?<![a-z0-9])" + body + r"(?![a-z0-9])")
    except re.error:  # pragma: no cover - escaped input cannot realistically fail
        return None


class Corpus:
    """The artifact's own text, in two forms, plus its visible-label vocabulary."""

    __slots__ = ("text", "squeezed_labels", "labels", "files", "bytes", "truncated")

    def __init__(self) -> None:
        self.text = ""
        self.squeezed_labels: set[str] = set()
        self.labels: list[str] = []
        self.files = 0
        self.bytes = 0
        self.truncated = False

    @property
    def usable(self) -> bool:
        return self.bytes >= MIN_CORPUS_BYTES

    def has(self, literal: str) -> bool:
        """Is ``literal`` present in the artifact as rendered text?

        Fails toward GROUNDED: every uncertain path returns True, because a
        false "grounded" costs nothing while a false "invented" spends a
        planner call and risks replacing a good plan.
        """
        n = _norm(literal)
        if len(n) < MIN_LITERAL_LEN:
            return True          # too short to be evidence either way
        rx = _literal_re(n)
        if rx is None:
            return True
        if rx.search(self.text):
            return True
        sq = _squeeze(n)
        return bool(sq) and sq in self.squeezed_labels


# Visible-label harvest. These are what the planner should be choosing FROM, so
# the inventory we hand back on a retry is built from the artifact rather than
# from the model's idea of the artifact.
_LABEL_SRC = (
    re.compile(r"<button[^>]*>(.*?)</button>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<a[^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<summary[^>]*>(.*?)</summary>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<h[1-4][^>]*>(.*?)</h[1-4]>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<label[^>]*>(.*?)</label>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<option[^>]*>(.*?)</option>", re.IGNORECASE | re.DOTALL),
    re.compile(r"""\b(?:aria-label|title|placeholder|alt)\s*=\s*["']([^"']{2,60})["']""",
               re.IGNORECASE),
    # Component dialects keep their labels in JS data, not in markup. Habitica's
    # whole nav is `{ to: '/inventory', text: 'Inventory' }` inside a .vue file,
    # so the tag patterns above harvested 0 labels from 73 files -- and an empty
    # inventory makes the retry message worthless exactly when it is needed.
    re.compile(r"""\b(?:text|label|title|name|placeholder|tooltip|caption|heading|"""
               r"""header|cta|button|linkText|displayName)\s*:\s*["']([^"']{2,60})["']""",
               re.IGNORECASE),
)
_TAG = re.compile(r"<[^>]+>")
_ENTITY = re.compile(r"&[#a-z0-9]{2,8};", re.IGNORECASE)
_MUSTACHE = re.compile(r"\{\{.*?\}\}|\{[^{}]*\}|\$\{[^{}]*\}", re.DOTALL)


def _harvest_labels(body: str, into: dict[str, None]) -> None:
    for rx in _LABEL_SRC:
        for m in rx.finditer(body):
            raw = _MUSTACHE.sub(" ", m.group(1) or "")
            raw = _ENTITY.sub(" ", _TAG.sub(" ", raw))
            lab = _WS.sub(" ", raw).strip()
            # Skip empties, giant blobs (a matched <a> wrapping a whole card),
            # and anything that is plainly code rather than a label.
            if 2 <= len(lab) <= 60 and not any(c in lab for c in "{}<>;=") \
                    and any(ch.isalnum() for ch in lab):
                into.setdefault(lab, None)


def build_corpus(workspace: str | os.PathLike | None,
                 extra_text: Iterable[str] = ()) -> Corpus:
    """Read every source file under ``workspace`` into a searchable corpus.

    Never raises. An unreadable or absent tree yields an unusable corpus, which
    every caller below treats as "no opinion".
    """
    c = Corpus()
    parts: list[str] = []
    labels: dict[str, None] = {}
    for t in extra_text:
        if t:
            parts.append(str(t))
    try:
        if workspace and os.path.isdir(workspace):
            for dp, dns, fns in os.walk(workspace):
                dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".git")]
                for fn in fns:
                    if os.path.splitext(fn)[1].lower() not in SRC_SUFFIXES:
                        continue
                    p = os.path.join(dp, fn)
                    try:
                        if os.path.getsize(p) > MAX_FILE_BYTES:
                            continue
                        with open(p, "r", errors="ignore") as fh:
                            body = fh.read()
                    except OSError:
                        continue
                    c.files += 1
                    parts.append(body)
                    _harvest_labels(body, labels)
                    c.bytes += len(body)
                    if c.bytes >= MAX_CORPUS_BYTES:
                        c.truncated = True
                        break
                if c.truncated:
                    break
    except Exception:  # noqa: BLE001 — fail open, see module docstring
        c.truncated = True
    blob = "\n".join(parts)
    c.text = _norm(blob)
    c.squeezed_labels = {_squeeze(l) for l in labels if _squeeze(l)}
    c.bytes = max(c.bytes, len(blob))
    c.labels = list(labels)
    return c


def target_literals(target: Any) -> list[str]:
    """Every text literal a selector could match on, one per union clause.

    A comma-union (`button:has-text("Add Task"), button:has-text("Create")`)
    resolves if ANY clause resolves, so the caller treats the whole target as
    grounded when any one literal is present.
    """
    if not target:
        return []
    t = str(target)
    out: dict[str, None] = {}
    for m in _ATTR_TEXT.finditer(t):
        out.setdefault(m.group(1), None)
    for m in _QUOTED.finditer(t):
        out.setdefault(m.group(1), None)
    if not out:
        # Bare prose (`AIRFOIL button`), the generator's dialect. Reuse the
        # planner's own label extractor so both spellings of one control are
        # judged identically.
        try:
            from scripts.interactive_verify.planner import target_label
        except ImportError:  # pragma: no cover - flat layout
            from planner import target_label  # type: ignore[no-redef]
        lab = target_label(t)
        # Only prose, never a CSS/xpath expression: `div.panel > input` names a
        # structure, and its absence from the source text proves nothing.
        if lab and not re.search(r"""["'#\[\]>:()=,*~+|]|\.\w|^\s*(?:xpath|css|id)\s*=""", t):
            out.setdefault(lab, None)
    return [s for s in out
            if MIN_LITERAL_LEN <= len(_norm(s)) <= MAX_LITERAL_LEN]


def check_plan(actions: list[dict[str, Any]], corpus: Corpus,
               authored: set[str] | None = None) -> dict[str, Any]:
    """Classify every text-bearing step as grounded / ungrounded / unchecked.

    Returns a report that is always safe to attach to plan.json. ``checked`` is
    0 whenever the corpus is unusable, which is the caller's signal to abstain.
    """
    authored = authored or set()
    # Every key is present from the start. The abstain path below returns early,
    # and a caller reading `rep["should_regenerate"]` on that dict must get False,
    # not a KeyError that the fail-open wrapper would then record as a scan crash
    # — an abstention and a broken scanner would become indistinguishable.
    rep: dict[str, Any] = {
        "corpus_files": corpus.files, "corpus_bytes": corpus.bytes,
        "corpus_truncated": corpus.truncated, "corpus_usable": corpus.usable,
        "checked": 0, "grounded": 0, "ungrounded": 0,
        "ungrounded_authored": 0, "invented": [], "skipped_reason": None,
        "planner_invented": 0, "invented_frac": 0.0, "should_regenerate": False,
    }
    if not corpus.usable:
        rep["skipped_reason"] = (
            f"corpus too small to prove absence ({corpus.bytes} bytes, "
            f"{corpus.files} files) — grounding not applied")
        return rep
    for i, a in enumerate(actions or []):
        if not isinstance(a, dict) or a.get("action") not in _TEXT_VERBS:
            continue
        lits = target_literals(a.get("target"))
        if not lits:
            continue
        rep["checked"] += 1
        if any(corpus.has(x) for x in lits):
            rep["grounded"] += 1
            continue
        rep["ungrounded"] += 1
        is_auth = any(_norm(x) in authored for x in lits)
        if is_auth:
            rep["ungrounded_authored"] += 1
            continue
        rep["invented"].append({
            "step": i, "action": a.get("action"),
            "target": str(a.get("target"))[:160],
            "literals": lits[:4],
            "note": str(a.get("note") or "")[:120],
            "for_item": a.get("for_item"),
        })
    n_plan = rep["checked"] - rep["ungrounded_authored"]
    rep["planner_invented"] = len(rep["invented"])
    rep["invented_frac"] = round(rep["planner_invented"] / n_plan, 3) if n_plan else 0.0
    rep["should_regenerate"] = bool(
        (rep["planner_invented"] >= MIN_UNGROUNDED
         and rep["invented_frac"] >= MIN_UNGROUNDED_FRAC)
        or rep["planner_invented"] >= MIN_UNGROUNDED_ABS)
    return rep


def authored_labels(items: list[dict[str, Any]] | None) -> set[str]:
    """Normalized labels the checklist itself authored (kept verbatim by contract)."""
    out: set[str] = set()
    for it in items or []:
        for step in (it.get("action_plan") or []):
            if isinstance(step, dict):
                for lit in target_literals(step.get("target")):
                    out.add(_norm(lit))
    return out


def _inventory(corpus: Corpus, limit: int = 120) -> list[str]:
    """The artifact's real label vocabulary, longest-first (most specific)."""
    seen: dict[str, None] = {}
    for lab in sorted(corpus.labels, key=lambda s: (-len(s), s.lower())):
        k = _norm(lab)
        if k and k not in seen:
            seen[k] = None
        if len(seen) >= limit:
            break
    return [l for l in corpus.labels if _norm(l) in seen][:limit]


def feedback_message(report: dict[str, Any], corpus: Corpus,
                     initial_obs: dict[str, Any] | None = None,
                     dom_chars: int = 14000) -> str:
    """The retry turn: name what was invented, then supply what actually exists.

    Deliberately blunt about the failure mode. A generic "some selectors did not
    match" invites the model to re-guess around the edges; naming the strings and
    the reason it produced them is what redirects it to the artifact in front of
    it.
    """
    inv = report.get("invented") or []
    lines = [
        "STOP. Your plan targets UI that does not exist in this artifact.",
        "",
        f"I searched the artifact's entire source tree ({corpus.files} files, "
        f"{corpus.bytes} bytes — every page and component, not just the landing "
        f"page you were shown). {len(inv)} of your targets name text that appears "
        f"NOWHERE in it:",
        "",
    ]
    for d in inv[:24]:
        lit = ", ".join(f'"{x}"' for x in (d.get("literals") or [])[:3])
        lines.append(f"  - step {d['step']} {d['action']}: {lit}"
                     + (f"   (you wrote: {d['note']})" if d.get("note") else ""))
    if len(inv) > 24:
        lines.append(f"  ... and {len(inv) - 24} more")
    lines += [
        "",
        "This is the known failure mode: when the build task names a product you "
        "recognise, it is easy to plan against the REAL product you remember "
        "instead of the artifact that was actually built. The artifact is usually "
        "a partial implementation — often a single page — and the navigation, "
        "screens and controls of the full product are simply not there. Steps that "
        "target them cannot resolve, and every checklist item that depends on them "
        "is scored INDETERMINATE, which is the worst outcome available: the "
        "artifact gets no credit for what it does do.",
        "",
        "Here is what the artifact actually contains.",
        "",
        "INTERACTIVE LABELS PRESENT IN THE SOURCE (buttons, links, headings, "
        "labels, options, aria-labels — the complete vocabulary you may target "
        "by text):",
    ]
    inventory = _inventory(corpus)
    lines.append("  " + "\n  ".join(f"- {l}" for l in inventory) if inventory
                 else "  (none found — this artifact exposes no text labels; "
                      "target CSS selectors or the canvas instead)")
    links = (initial_obs or {}).get("links")
    if links:
        lines += ["", "PAGES REACHABLE IN THIS ARTIFACT (use goto{url} to visit):"]
        for u in list(links)[:40]:
            lines.append(f"  - {u}")
    dom = (initial_obs or {}).get("dom_snapshot") or ""
    if dom:
        lines += ["", f"LANDING PAGE DOM (extended to {dom_chars} chars):", dom[:dom_chars]]
    lines += [
        "",
        "Now produce the COMPLETE replacement plan, same JSON format.",
        "Rules for this attempt:",
        "  1. Every text target must come from the label list above, or be a CSS "
        "selector you can point to in the DOM. Do not invent labels.",
        "  2. Keep any step tagged `for_item` and its `target` VERBATIM — those "
        "come from the checklist, not from you, even if the label is absent.",
        "  3. If a checklist item's UI genuinely does not exist in this artifact, "
        "still emit its step against the closest control that DOES exist, so the "
        "judge sees real evidence of what the page does instead of an error.",
        "  4. Do not simply delete the failing steps — a shorter plan measures "
        "less and scores worse.",
        '  5. End with {"action": "conclude"}.',
    ]
    return "\n".join(lines)
