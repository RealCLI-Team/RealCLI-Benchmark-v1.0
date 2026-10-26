#!/usr/bin/env python3
"""Adapter that drives the vendored htmleval pipeline to produce an evidence bundle.

``build_exec_fn`` returns an ``exec_fn(workspace_path) -> dict`` that reads the
artifact's entry HTML, runs htmleval's browser-execution phases (with the VLM
scoring phase disabled — we judge with our own checklist), and maps the phase
outputs to the evidence bundle ``engine.verify`` expects:

    {renderable, code, dom, frames, trace}

htmleval needs Playwright/Chromium/browser-use, which live only in the evaluation environment
verify container — so this module lazy-imports htmleval inside ``exec_fn``. Importing
this module (to discover ``build_exec_fn``) never imports htmleval, so unit tests
run without a browser.
"""

from __future__ import annotations

import asyncio
import re
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from service import llm_client as _llm

_VENDOR = Path(__file__).resolve().parent / "vendor"

# preferred entry filenames when a workspace has several .html files
_ENTRY_PREF = ("index.html", "main.html", "app.html")
# The two tags that separate a document from a partial — see `_is_document`.
# Matched against bytes so no decode can fail on a mixed-encoding build.
_HTML_TAG_RE = re.compile(rb"<html[\s>]", re.I)
_BODY_TAG_RE = re.compile(rb"<body[\s>]", re.I)
# Directories that hold DEPENDENCY html, never the artifact's own entry. A plain
# rglob picks up a package's bundled demo/test page (e.g.
# node_modules/three/examples/*.html) and hands it to the verifier as if it were
# the build — see _pick_entry. Same skip set the Harbor grader uses, where the
# mirror-image bug (node_modules-only trees scoring reward=1) was already fixed.
_DEP_DIRS = frozenset({"node_modules", "bower_components", "vendor",
                       "jspm_packages", ".git", ".hg", ".svn",
                       "__pycache__", ".cache"})

# DEFECT 96. Directory PREFIXES that hold a framework's build-internal HTML —
# server-side prerender output, not a servable entry. `.next/server/app/index.html`
# is real markup: it paints, `renderable` comes back True, and the accessibility
# tree at load looks exactly like the app. It is not the app. There is no client
# bundle, no router and no hydration, so every link 404s and every control is
# inert — which the verifier records as "the interaction could not be performed"
# and, by DEGRADE-NEVER-GATE, abstains on. On eval25_o5 `AcEnV8F` that meant 11 of
# 12 items abstained, the one ambient item passed on the AX tree alone, and a
# Next.js app that was never served scored reward=1.0000.
#
# Matched as an ORDERED PREFIX of the parent parts, not as a member set like
# `_DEP_DIRS`: a Next STATIC EXPORT is a legitimate artifact and lands in `out/`,
# and `.next/` itself may hold nothing else we care about, so excluding the whole
# top-level directory would be both too broad and — for `dist/.vite` — too narrow.
_BUILD_INTERNAL_PREFIXES = (
    (".next", "server"), (".next", "static"), (".next", "cache"),
    (".nuxt", "dist", "server"), (".svelte-kit", "output", "server"),
    (".svelte-kit", "generated"), ("dist", ".vite"), (".parcel-cache",),
    (".angular", "cache"), (".turbo",), (".vercel", "output"),
    # DEFECT 96b -- static-site-generator SOURCE partials. Jekyll/Hugo/11ty keep
    # their Liquid/Go templates in these dirs; every file there is a fragment by
    # definition, never a servable page. The BUILT site lands in `_site`/`public`,
    # both already in `_BUILD_DIRS`, so a real build still wins outright.
    #
    # Measured on eval25_o5 `AsLY5Xz`: the agent shipped a Jekyll *theme gem*
    # (Gemfile, .gemspec, `_sass`, `_skins`) whose 13 .html files are ALL Liquid
    # partials -- no `index.html`, no `_site/`, nothing servable. `_is_document`
    # rejected every one, so the fallback returned `htmls[0]`, the shortest path:
    # `_includes/toc.html`. That partial parses and paints, so `renderable=True`,
    # and then all 16 nav probes missed (`selector not found` on every one) because
    # a table-of-contents fragment has no nav. 11 of 12 items went indeterminate.
    # The honest verdict for a tree with no page in it is NoArtifact, which is what
    # emptying the candidate list produces.
    ("_includes",), ("_layouts",), ("_partials",), ("_templates",),
)


def _is_build_internal(parts: tuple[str, ...]) -> bool:
    """True when a candidate's parent directories start with a build-internal prefix.

    Returning no entry at all is the correct outcome when this leaves nothing:
    the caller raises `NoArtifact`, which is a DETERMINATE 0.0 with a stated
    reason, and that is honest for a static-file verifier handed an app that only
    exists behind `next start`. A false 0.0 you can read is strictly better than a
    1.0000 nobody can explain."""
    low = tuple(s.lower() for s in parts)
    return any(low[:len(pref)] == pref for pref in _BUILD_INTERNAL_PREFIXES)


def _run_coro(coro: Any) -> Any:
    """Run an async coroutine to completion even if the current thread already has
    a running event loop (sync_playwright starts one in the worker). We execute
    the coroutine on a brand-new loop in a dedicated thread so it never touches
    the thread's existing loop.

    This prevents RuntimeError: asyncio.run() cannot be called from a running event loop
    and ensures the coroutine is always awaited (no "never awaited" warning).
    """
    result = {}

    def _runner():
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            result["value"] = loop.run_until_complete(coro)
        except BaseException as e:  # capture to re-raise on caller thread
            result["error"] = e
        finally:
            loop.close()

    t = threading.Thread(target=_runner)
    t.start()
    t.join()
    if "error" in result:
        raise result["error"]
    return result["value"]


def _ensure_vendor_on_path() -> None:
    p = str(_VENDOR)
    if p not in sys.path:
        sys.path.insert(0, p)


def _uid_for(ws: Path) -> str:
    """A stable, unique id for an artifact from its workspace path — used as the
    htmleval game_id + scratch subdir so parallel/sequential artifacts (whose dirs
    are ALL named 'workspace') never share a render output dir. Includes the parent
    (record_key) dir name for readability plus a hash of the full path for uniqueness."""
    import hashlib
    full = str(ws.resolve())
    h = hashlib.md5(full.encode("utf-8")).hexdigest()[:10]
    parent = ws.parent.name or "task"      # the record_key dir
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in parent)[:40]
    return f"{safe}-{h}"


def _is_document(p: Path) -> bool:
    """Does this file open BOTH `<html>` and `<body>` — i.e. is it a document?

    The discriminator for defect 53. Cheap and deliberately shallow: read a
    prefix and look for the two tags a browser needs before it has a document to
    render. A partial carrying only `<head>` (or only a `<body>` continuation)
    fails, which is the whole point.

    The 512 KB cap bounds the read on a workspace that might hold a large
    bundle; every single-file artifact we ship opens `<body>` far inside that.
    Unreadable ⇒ False: a file we cannot read is not a document we can render.
    """
    try:
        blob = p.open("rb").read(512_000)
    except Exception:  # noqa: BLE001
        return False
    return bool(_HTML_TAG_RE.search(blob)) and bool(_BODY_TAG_RE.search(blob))


# Where a bundler writes its output. Checked as the FIRST path segment only, so
# a source file that merely lives under `src/dist-notes/` cannot masquerade.
_BUILD_DIRS = frozenset({"dist", "build", "out", "_site", "public"})

# The tell of an UNBUILT bundler entry: a module <script> pointing into the
# source tree. `vite build` rewrites exactly this line to `/assets/<name>-<hash>.js`,
# so its presence means the file in front of us is the input, not the output.
_SRC_STUB_RE = re.compile(
    rb"""<script[^>]{0,200}?\btype\s*=\s*["']module["'][^>]{0,200}?"""
    rb"""\bsrc\s*=\s*["']\.?/?src/""",
    re.I | re.S)


def _is_src_stub(p: Path) -> bool:
    """Is this HTML a bundler's INPUT (`<script type=module src="/src/main.js">`)?

    Read as bytes and head-only for the same reason `_is_document` is: the entry
    decision must not be able to fail on a mixed-encoding build, and the tag we
    are looking for is always in the first few KB."""
    try:
        with p.open("rb") as fh:
            blob = fh.read(65536)
    except Exception:  # noqa: BLE001
        return False
    return bool(_SRC_STUB_RE.search(blob))


def _prefer_built(matches: list[Path], ws: Path) -> Path:
    """Among same-named entries, skip an unbuilt stub in favour of the build.

    DEFECT 85. The sort above is shallowest-first, so a repo-root `index.html`
    always beats `dist/index.html` — and for a Vite/Rollup/Parcel artifact the
    root one is the file the bundler CONSUMES. It has no application code in it:
    a title, a `<div id="root">`, and `<script type="module" src="/src/main.js">`.
    Rendering it gets a static shell and nothing else, because the browser reads
    the source tree the bundler was supposed to compile — bare specifiers and all.

    Measured on the live claude-opus-5 run, `task_0000`: we graded the 583-byte
    root stub while a complete `dist/` sat beside it, and the model was charged a
    0.000 for a build it had actually produced. Note this compounds with DEFECT 84
    rather than duplicating it — with the site-root fix the stub's `/src/main.js`
    now LOADS, and then dies on `Failed to resolve module specifier "three"`,
    because an unbuilt entry has no importmap. Either fix alone only moves the
    failure; the artifact is only graded correctly when the built entry is chosen
    AND its root-absolute assets resolve.

    Conservative by construction: it only ever fires when the leading candidate
    is provably a bundler input AND a same-named entry exists under a build
    directory AND that one is not itself a stub. Anything else returns the
    original choice, so no artifact that resolves correctly today can move."""
    lead = matches[0]
    if not _is_src_stub(lead):
        return lead
    for f in matches[1:]:
        parts = f.relative_to(ws).parts[:-1]
        if parts and parts[0].lower() in _BUILD_DIRS and not _is_src_stub(f):
            return f
    return lead


def _pick_entry(ws: Path) -> Path | None:
    """The artifact's entry HTML, or None when it shipped no ``index.html`` at
    the workspace root.

    This instance's delivery contract (script.json delivery_format slice +
    oracle_contract delivery_medium_index_html) is strict: the shipped
    artifact is a single self-contained file named exactly ``index.html`` at
    the workspace root, the eval harness loads exactly that file and nothing
    else, and any other filename or subpath scores zero. The generic fallback
    machinery (``_ENTRY_PREF`` alternates at any depth, largest-document /
    first-html fallback) exists for corpora without that promise; here it
    silently graded ``main.html``/``app.html`` copies or a subdirectory
    ``index.html`` instead of producing the contracted NoArtifact -> 0.0.
    The caller (iv_verify._build_task) maps None to NoArtifact -> reward 0.0.
    """
    entry = ws / "index.html"
    if entry.is_file():
        return entry
    return None

def _resolve_evaluator_cfg(evaluator_cfg: dict[str, Any] | None) -> dict[str, str]:
    """Resolve evaluator identity strictly from the given evaluator cfg.

    Returns dict with 'base_url', 'api_key', 'model' keys. There are NO service
    defaults and no fallbacks: a missing value must fail loudly instead of
    silently retargeting the evaluator model.
    """
    cfg = evaluator_cfg or {}
    missing = [k for k in ("base_url", "api_key", "model") if not cfg.get(k)]
    if missing:
        raise RuntimeError(
            "evaluator cfg missing required keys: " + ", ".join(missing)
            + " — no fallback allowed. Populate judge_cfg (base_url/api_key/model)."
        )
    base_url = cfg["base_url"]
    api_key = cfg["api_key"]
    model = cfg["model"]

    return {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
    }


def build_exec_fn(record_timeout: int = 300, skip_agent_phase: bool = False,
                  scratch_dir: str | None = None) -> Callable[[str], dict]:
    """Return an ``exec_fn(workspace_path)`` bound to a fresh htmleval config.

    ``scratch_dir`` is where htmleval writes its own reports/screenshots. Default
    is a sibling ``_htmleval`` of the artifact workspace; pass the verify artifact
    dir instead so the rolling output tree is never polluted (the engine does this).
    """

    def exec_fn(workspace_path: str) -> dict[str, Any]:
        ws = Path(workspace_path)
        entry = _pick_entry(ws)
        if entry is None:
            return {"renderable": False, "code": "", "dom": "", "frames": [],
                    "trace": [], "error": "no HTML entry in workspace"}
        code = entry.read_text(encoding="utf-8", errors="replace")
        try:
            _ensure_vendor_on_path()

            from htmleval import EvalConfig, EvalContext, build_pipeline  # type: ignore

            # CRITICAL: every artifact's workspace dir is literally named "workspace",
            # so a naive game_id=ws.name collides — htmleval would write every render
            # into the SAME reports/<game_id>_default/ dir and _to_evidence would pick
            # up STALE frames from a previously-verified artifact (wrong screenshots →
            # bogus verdict). Derive a UNIQUE id from the full workspace path so each
            # artifact gets its own report dir, and give each a private scratch root.
            uid = _uid_for(ws)
            base_scratch = scratch_dir or str(ws.parent / "_htmleval")
            config = EvalConfig()
            config.processing.skip_vision_phase = True          # we bring our own judge
            config.processing.skip_agent_phase = skip_agent_phase
            config.processing.record_timeout = record_timeout
            config.workspace = str(Path(base_scratch) / uid)    # per-artifact scratch

            pipeline = build_pipeline(config)                   # standalone Chromium
            ctx = EvalContext(query="", response=code, game_id=uid)
            ctx = _run_coro(pipeline.evaluate(ctx))
            evidence = _to_evidence(ctx, code)
            # Add entry_url (file:// URL of the entry HTML) for agent_judge
            evidence["entry_url"] = entry.as_uri()
            return evidence
        except Exception as exc:  # noqa: BLE001 - any failure => not renderable
            return {"renderable": False, "code": code, "dom": "", "frames": [],
                    "trace": [], "error": f"htmleval exec failed: {exc}"[:300]}

    return exec_fn


def run_vision_eval(workspace_path: str, evaluator_cfg: dict[str, Any], *,
                    record_timeout: int = 600, scratch_dir: str | None = None) -> dict[str, Any]:
    """Run htmleval's vendored pipeline with VisionEval ON to score the artifact's 5 dimensions.

    Mirrors build_exec_fn's exec_fn BUT with skip_vision_phase=False and skip_agent_phase=True.
    Routes evaluator_cfg (dict with base_url/api_key/model) into htmleval's config.evaluator.

    After pipeline.evaluate(ctx), reads ctx.final_score (expected to be a dict with
    'total_score' and the 5 dims: rendering, visual_design, functionality, interaction,
    code_quality — each may be an int or {score, reason} sub-object).

    Returns: {method, renderable, score, total_score, dims, report, frames}
    On ANY exception: {method, renderable=False, score=0.0, error}
    """
    ws = Path(workspace_path)
    entry = _pick_entry(ws)
    if entry is None:
        return {
            "method": "htmleval",
            "renderable": False,
            "score": 0.0,
            "error": "no HTML entry in workspace",
        }
    code = entry.read_text(encoding="utf-8", errors="replace")
    try:
        _ensure_vendor_on_path()

        from htmleval import EvalConfig, EvalContext, build_pipeline  # type: ignore

        uid = _uid_for(ws)
        base_scratch = scratch_dir or str(ws.parent / "_htmleval")
        config = EvalConfig()
        config.processing.skip_vision_phase = False  # ENABLE VisionEval scoring
        config.processing.skip_agent_phase = True    # skip agent phase
        config.processing.record_timeout = record_timeout
        config.workspace = str(Path(base_scratch) / uid)

        # Route evaluator_cfg into config.evaluator with fallbacks to service defaults
        resolved = _resolve_evaluator_cfg(evaluator_cfg)
        config.evaluator.base_url = resolved["base_url"]
        config.evaluator.api_key = resolved["api_key"]
        config.evaluator.model = resolved["model"]

        # Keep agent config consistent (skip_agent_phase=True so agent isn't used,
        # but set credentials to avoid empty-config validation issues)
        config.agent.base_url = resolved["base_url"]
        config.agent.api_key = resolved["api_key"]
        config.agent.model = resolved["model"]

        pipeline = build_pipeline(config)
        ctx = EvalContext(query="", response=code, game_id=uid)
        ctx = _run_coro(pipeline.evaluate(ctx))

        # Extract final_score (5-dim dict from VisionEval)
        final_score = ctx.final_score or {}
        total_score = final_score.get("total_score", 0)

        # Extract individual dimension scores (may be int or {score, reason})
        dims = {}
        for dim in ["rendering", "visual_design", "functionality", "interaction", "code_quality"]:
            val = final_score.get(dim, 0)
            # If it's a dict with "score" key, extract the numeric score
            if isinstance(val, dict):
                dims[dim] = val.get("score", 0)
            else:
                dims[dim] = val

        # Collect frames
        frames: list[str] = []
        out_dir = getattr(ctx, "output_dir", None)
        if out_dir:
            frames = [str(p) for p in sorted(Path(out_dir).glob("frame_*.png"))]

        # Try to extract report.md from output_dir if available
        report = ""
        if out_dir:
            report_path = Path(out_dir) / "report.md"
            if report_path.exists():
                report = report_path.read_text(encoding="utf-8", errors="replace")

        return {
            "method": "htmleval",
            "renderable": bool(ctx.get_phase("render_test") and ctx.get_phase("render_test").data.get("rendered", False)),
            "score": total_score / 100.0 if total_score else 0.0,
            "total_score": total_score,
            "dims": dims,
            "report": report,
            "frames": frames,
            # VLM token usage accumulated across all vision-eval agent calls; the
            # scoring pipeline reads this via judge._call_vlm to log real tokens.
            "_usage": getattr(ctx, "vlm_usage", None) or {},
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "method": "htmleval",
            "renderable": False,
            "score": 0.0,
            "error": str(exc)[:300],
        }


def _to_evidence(ctx: Any, code: str) -> dict[str, Any]:
    render = ctx.get_phase("render_test")
    static = ctx.get_phase("static_analysis")
    agent = ctx.get_phase("agent_test")
    rdata = (render.data if render else {}) or {}
    sdata = (static.data if static else {}) or {}
    adata = (agent.data if agent else {}) or {}

    renderable = bool(rdata.get("rendered", False))
    frames: list[str] = []
    out_dir = getattr(ctx, "output_dir", None)
    if out_dir:
        frames = [str(p) for p in sorted(Path(out_dir).glob("frame_*.png"))]

    trace = adata.get("actions") or rdata.get("frame_annotations") or []
    dom = sdata.get("dom_summary") or rdata.get("dom_inventory") or ""

    return {
        "renderable": renderable,
        "code": ctx.html_code or code if hasattr(ctx, "html_code") else code,
        "dom": str(dom)[:8000],
        "frames": frames,
        "trace": trace,
        "console_errors": rdata.get("console_errors", []),
        "page_errors": rdata.get("page_errors", []),
    }
