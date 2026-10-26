#!/usr/bin/env python3
"""Serve extracted artifacts over local HTTP instead of ``file://``.

WHY THIS EXISTS
───────────────
Chromium enforces CORS on ES-module fetches (``<script type="module">`` and
dynamic ``import()``), and the ``file://`` origin is *opaque* — so every
module-load is blocked and the app silently never boots. In the b0811 game
testbed this hit **63% of substantive games** (they use ES modules / importmap
via three.js), which then scored ~0 not because they were broken but because
the verifier loaded them from ``file://``.

Proof (identical files, only the URL scheme differs):

    file:// -> main.js request FAILS, app stuck at initial DOM
    http:// -> module chain loads, app boots

Serving over ``http://127.0.0.1`` gives the page a normal, same-origin origin,
so modules load exactly as they did in the build environment (which is why the
platform's own render check passed). This also fixes AXTree checklist grounding,
which renders the same URL.

DESIGN
──────
ONE process-wide daemon server rooted at the filesystem root (``/``). Any
absolute artifact path maps to ``http://127.0.0.1:<port><abs-path>``, and
relative asset references (``./src/main.js``) resolve under the artifact's own
directory exactly as a real static host would serve them. Rooting at ``/`` is
safe here — the box is local, single-user, and the server binds ``127.0.0.1``
only. It is also a strict improvement over ``file://``: any root-absolute asset
(``/assets/x.png``) was *already* broken under ``file://`` (it resolved against
the filesystem root there too), so no artifact renders worse and module-based
ones render correctly.

The server is started lazily on first use and lives for the process lifetime
(daemon threads), so a batch CLI needs no per-task server lifecycle and cannot
leak a growing pool of sockets across hundreds of instances.
"""
from __future__ import annotations

import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit

# Guarded by _LOCK; populated once on first to_http_url() call.
_LOCK = threading.Lock()
_SERVER: ThreadingHTTPServer | None = None
_PORT: int | None = None

# DEFECT 84 -- site roots registered by to_http_url(), most-recent-first.
#
# Rooting the server at "/" makes every RELATIVE asset reference resolve, which
# is what the docstring above reasons about. It leaves ROOT-ABSOLUTE ones
# (`/assets/index-DoGutudt.js`, `/src/main.js`) resolving against the filesystem
# root, where they 404 -- and the docstring dismissed that as "already broken
# under file://", which is true and irrelevant: a real static host serves them,
# and every bundler emits them by default. `vite build` writes
# `<script type="module" src="/assets/index-<hash>.js">` into dist/index.html;
# that is the SHIPPED artifact and it cannot boot here.
#
# Measured on the live claude-opus-5 run, `task_0000`: the page requested its own
# `/src/main.js`, we served 404, and the verifier reported a `missing-own-asset`
# build defect -- "the shipped bundle is incomplete" -- about a bundle that was
# complete. Reward 0.000, and the record blamed the model.
#
# The fix is additive and cannot regress anything: a request is served exactly as
# before whenever the filesystem-absolute path EXISTS. Only a request that would
# otherwise 404 gets a second chance, resolved under the site root of the page
# that asked for it (matched via Referer, which same-origin requests carry in
# full under the default referrer policy).
_SITE_ROOTS: list[str] = []


class _QuietHandler(SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler that (1) never spams stdout/stderr with a line
    per request — a batch renders thousands of assets — and (2) serves every
    script-ish extension with a JavaScript MIME type. The second point is
    load-bearing: browsers refuse to execute an ES module served as anything
    other than a JavaScript type, so an incorrect MIME would reintroduce the
    very failure this server exists to fix.

    DEFECT 42 — the extensions this map omits do not fall back to "unknown";
    they fall through to the SYSTEM mime table (``/etc/mime.types``), which has
    opinions that predate the web. On this box ``.ts`` resolves to
    ``text/vnd.trolltech.linguist`` (a Qt translation-source file) and ``.mts``
    to ``model/vnd.mts``, so Chromium refused the module outright:

        Failed to load module script: Expected a JavaScript-or-Wasm module
        script but the server responded with a MIME type of
        "text/vnd.trolltech.linguist". Strict MIME type checking is enforced
        for module scripts per HTML spec.

    The app then never boots, its start gate never lifts, every later click is
    intercepted by that gate, and the artifact takes a 0.0 that reads as bad
    authoring. Measured on the b0811 v011 run: 4 tasks, all scoring exactly
    0.000. A serving bug on our side is the worst possible cause of a zero, so
    the map is now explicit for every extension an artifact might import —
    a WRONG type is strictly worse than a missing one, and silence from the
    system table is how a wrong type gets in.
    """

    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".cjs": "text/javascript",
        # TypeScript/JSX sources. Browsers cannot execute the *syntax*, but an
        # artifact that imports them either ships a transpiler (esbuild-wasm,
        # sucrase, a <script type="module"> shim) or is broken on its own terms.
        # Either way the fetch must not be the thing that fails: with a JS type
        # the artifact's own loader gets its chance, and a genuine syntax error
        # then surfaces as the artifact's defect rather than ours.
        ".ts": "text/javascript",
        ".mts": "text/javascript",
        ".cts": "text/javascript",
        ".tsx": "text/javascript",
        ".jsx": "text/javascript",
        # Named explicitly rather than trusted to the system table, which varies
        # by image. `.wasm` in particular is the other type strict module
        # checking accepts, and `.json` is required for import assertions.
        ".json": "application/json",
        ".wasm": "application/wasm",
        ".css": "text/css",
        ".webmanifest": "application/manifest+json",
        ".map": "application/json",
        # Shader sources are usually fetched as text; no system entry exists, and
        # a missing type makes some fetch wrappers guess wrong.
        ".glsl": "text/plain",
        ".vert": "text/plain",
        ".frag": "text/plain",
    }

    def log_message(self, *args: object) -> None:  # noqa: D401 — silence per-request logs
        return

    def handle_one_request(self) -> None:
        """Serve one request, treating a client hangup as normal, not an error.

        A renderer that navigates away, or is killed by the step watchdog, closes
        its sockets while requests are still in flight. socketserver then prints a
        full two-part traceback to stderr for each one, and the first half names
        whatever the request happened to be -- so a routine disconnect reads like a
        server fault. Observed on the b0811 sweep as:

            OSError: [Errno 36] File name too long: '<ws>/css/url(data:image/png;base64,iVBOR...
            During handling of the above exception, another exception occurred:
            BrokenPipeError: [Errno 32] Broken pipe

        Both halves are benign. The long name is an artifact whose CSS contains a
        malformed `url(data:...)` that the browser resolved as a relative path;
        404 is the correct answer and the base class already produces it. The
        broken pipe is only the 404 body failing to reach a client that had
        already gone. The task in question completed and wrote its verdict.

        Swallowed rather than logged because these tracebacks are pure noise in a
        sweep log, and noise is expensive here: it is what a real serving fault
        would have to be spotted against."""
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True

    def translate_path(self, path: str) -> str:
        """Filesystem path for a request, with a site-root fallback (DEFECT 84).

        First answer is always the base class's: the server is rooted at "/", so
        the request path IS an absolute filesystem path, and if that file exists
        it is served unchanged. That keeps every artifact that works today
        working, byte for byte.

        Only when it does not exist do we ask the other question: is this a
        root-absolute asset reference belonging to some page we are serving? The
        Referer names the document (or the module) that issued the request, and
        the longest registered site root that prefixes it is that page's own
        document root -- exactly what a static host would have used. `/assets/x.js`
        from `<ws>/dist/index.html` then resolves to `<ws>/dist/assets/x.js`.

        Never raises: any failure falls through to the base answer, i.e. a 404,
        which is the behaviour before this existed."""
        fs = super().translate_path(path)
        try:
            if os.path.exists(fs):
                return fs
            rel = os.path.relpath(os.path.abspath(fs), os.sep)
            if rel.startswith(os.pardir):
                return fs
            for root in _candidate_roots(self.headers.get("Referer")):
                cand = os.path.join(root, rel)
                # Containment check: `rel` is already normalised by the base
                # class (it collapses "..") but the join is cheap to verify and
                # a traversal out of the site root is never a legitimate asset.
                if os.path.exists(cand) and os.path.abspath(cand).startswith(root + os.sep):
                    return cand
        except Exception:  # noqa: BLE001 — a fallback must never break a request
            pass
        return fs


def _candidate_roots(referer: str | None) -> list[str]:
    """Registered site roots to try for a request, best guess first.

    The roots that PREFIX the referring document's path come first, longest
    first: that is the page's own document root, and the longest match is the
    innermost one (`<ws>/dist` beats `<ws>` for a request from
    `<ws>/dist/index.html`). Everything else follows most-recent-first, which
    covers a request with no Referer at all -- the entry document itself, or a
    fetch from a worker -- where the artifact being verified right now is the
    only sensible guess.
    """
    with _LOCK:
        roots = list(_SITE_ROOTS)
    ref_path = ""
    if referer:
        try:
            ref_path = urlsplit(referer).path or ""
        except Exception:  # noqa: BLE001
            ref_path = ""
    if not ref_path:
        return roots
    hit = sorted((r for r in roots if ref_path.startswith(r + "/")),
                 key=len, reverse=True)
    return hit + [r for r in roots if r not in hit]


def _register_site_root(root: str) -> None:
    """Remember an artifact's document root, most-recent-first, bounded.

    Bounded because a batch CLI calls to_http_url() once per instance and this
    list must not grow with the run. 32 is far more than the number of artifacts
    whose assets can be in flight at once, and dropping the oldest entry can only
    cost a fallback for a page nobody is looking at any more."""
    with _LOCK:
        if root in _SITE_ROOTS:
            _SITE_ROOTS.remove(root)
        _SITE_ROOTS.insert(0, root)
        del _SITE_ROOTS[32:]


def _ensure_server() -> int:
    """Start the shared root-served HTTP server once; return its port."""
    global _SERVER, _PORT
    with _LOCK:
        if _SERVER is not None and _PORT is not None:
            return _PORT
        # directory="/" -> any absolute path is reachable as http://host:port/abs.
        handler = partial(_QuietHandler, directory="/")
        # Port 0 -> the OS assigns a free ephemeral port. Bind loopback only so the
        # artifacts are never exposed off-box.
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True,
                                  name="iv-static-server")
        thread.start()
        _SERVER = server
        _PORT = server.server_address[1]
        return _PORT


def to_http_url(path: str | Path) -> str:
    """Return an ``http://127.0.0.1:<port>/…`` URL that serves ``path``.

    Use this everywhere the verifier would otherwise navigate to
    ``Path(path).as_uri()`` — it is a drop-in replacement that lets ES-module and
    importmap artifacts actually boot. ``path`` must be an existing absolute file
    path (both loaders extract into an absolute workspace before calling this)."""
    port = _ensure_server()
    abs_path = Path(path).resolve()
    # The page's own document root, for root-absolute asset references — see
    # DEFECT 84 and `translate_path`. The directory holding the entry is what a
    # static host would have been pointed at, and it is right for both artifact
    # shapes we see: `<ws>/index.html` serving `/src/main.js`, and
    # `<ws>/dist/index.html` serving `/assets/index-<hash>.js`.
    _register_site_root(str(abs_path.parent))
    # quote each segment (spaces, unicode, '#', '?') but keep the '/' separators.
    encoded = quote(abs_path.as_posix())
    return f"http://127.0.0.1:{port}{encoded}"
