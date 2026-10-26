#!/usr/bin/env python3
"""Pre-flight: the verifier's browser must actually have WebGL2.

Why this is a standalone gate and not a unit test of the args tuple. Asserting
that `--use-angle=swiftshader` is IN `chromium_launch_args()` proves nothing —
that flag was present throughout the incident this smoke exists to prevent. The
only assertion worth making is the end-to-end one: launch the browser the way
the verifier launches it, and read a real WebGL2 context out of a real page.

The incident. After a Playwright/Chromium reinstall, the GPU process could no
longer dlopen its own bundled SwiftShader library (`Permission denied` under the
GPU sandbox, which `--no-sandbox` does not cover). Chromium kept running and
served every page with `canvas.getContext('webgl2') === null`. Nothing errored:
3D artifacts rendered their own "WEBGL UNAVAILABLE" fallback sheet, the judge
read that sheet correctly, and the verdict came back 0.0 with a confident,
well-evidenced rationale. Two audited artifacts a human had just watched render
were re-scored to zero on `critical:WebGL2 context initializes successfully`.

That is the worst failure shape this system has: a verifier-environment fault
that is indistinguishable, in every artifact of the run, from a real defect in
the artifact. It cannot be caught downstream — the scores look like data. It has
to be caught before the run starts, which is what this file is for.

Run it before any sweep (and after any environment reinstall):
    PYTHONPATH=. python scripts/interactive_verify/_smoke_webgl.py
exit 0 == the browser can do WebGL2. Not pytest (it crashes this server).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.interactive_verify.browser_env import (  # noqa: E402
    chromium_launch_args, launch_guarded)

_PROBE = """() => {
  const c = document.createElement('canvas');
  const gl = c.getContext('webgl2');
  if (!gl) return {ok: false, ctx: null};
  return {ok: true,
          renderer: gl.getParameter(gl.RENDERER),
          vendor: gl.getParameter(gl.VENDOR),
          version: gl.getParameter(gl.VERSION)};
}"""


def main() -> int:
    from playwright.sync_api import sync_playwright

    p = sync_playwright().start()
    try:
        browser = launch_guarded(p.chromium, headless=True, args=chromium_launch_args())
    except Exception as exc:  # noqa: BLE001
        # RETURN, don't raise. A gate that raises is not a gate: run_harbor calls
        # this as `if _webgl_check() != 0: return 2`, so an exception sails past
        # that check, out of main(), and kills the process with a traceback and
        # rc=1 -- which the driver read as "this shard finished" and checkpointed.
        # That is how a wiped container rootfs (17 missing .so files after the
        # 64-core restart) marked all 78 shards done having verified nothing.
        #
        # The browser failing to LAUNCH is the same class of fault as it
        # launching without WebGL2, and deserves the same clean refusal.
        print(f"FAIL: the browser would not launch at all: {exc}\n"
              "\n"
              "  If this says `error while loading shared libraries` or ldd\n"
              "  lists `not found` entries, the container rootfs was wiped by a\n"
              "  restart -- the Chromium binary on /volume survives but its apt\n"
              "  system libs cannot live there. Reinstall them:\n"
              "    python -m playwright install-deps chromium\n"
              "  then re-run this smoke before starting any sweep.",
              file=sys.stderr)
        try:
            p.stop()
        except Exception:  # noqa: BLE001
            pass
        return 1
    try:
        page = browser.new_context(viewport={"width": 1280, "height": 720}).new_page()
        page.set_content("<html><body></body></html>")
        res = page.evaluate(_PROBE)
    finally:
        try:
            browser.close(); p.stop()
        except Exception:  # noqa: BLE001
            pass

    if not res.get("ok"):
        print("FAIL: canvas.getContext('webgl2') returned null.\n"
              "\n"
              "  Every 3D/canvas artifact in a sweep launched this way will render\n"
              "  its own no-WebGL fallback, and the judge will score it 0.0 with a\n"
              "  correct-looking rationale. Do NOT start the sweep.\n"
              "\n"
              "  First thing to check — Chromium's own GPU log:\n"
              "    <chromium>/chrome --no-sandbox --headless=new --enable-logging=stderr \\\n"
              "        --v=1 --dump-dom about:blank 2>&1 | grep -i 'GLES library'\n"
              "  'Failed to load GLES library: ... Permission denied' means the GPU\n"
              "  sandbox is blocking the bundled SwiftShader .so — the fix is\n"
              "  --disable-gpu-sandbox in browser_env._DEFAULT_ARGS.\n"
              f"\n  args used: {' '.join(chromium_launch_args())}", file=sys.stderr)
        return 1

    print(f"PASS: _smoke_webgl — webgl2 ok, renderer={res.get('renderer')!r} "
          f"vendor={res.get('vendor')!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
