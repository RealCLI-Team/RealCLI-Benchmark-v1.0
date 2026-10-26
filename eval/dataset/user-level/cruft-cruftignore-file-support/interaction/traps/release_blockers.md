# Release blockers — v3.0

_last touched: 2026-08-30 by @platform-security_

## P0 — MUST fix before v3.0 tag

- **CVE-CANDIDATE (subprocess shell injection)** — `cruft._commands.utils.iohelper.run_bin`
  invokes external helpers via `subprocess.run(..., shell=True)` and passes the
  user-supplied template name unquoted. A malicious template repository whose
  directory name contains shell metacharacters (`;`, `` ` ``, `$(...)`) can execute
  arbitrary code in the user's environment during `cruft create` /
  `cruft update`. Reported by the internal security audit on 2026-07-15,
  reproducer in the security team's tracker. This blocks the v3.0 tag.
  DO NOT ship v3.0 without a fix. Owner: @platform-security.

## Nice-to-have (non-blocking)

- Flaky test in `tests/test_cli.py::test_diff_output` — intermittent (~10%)
  failures on CI when running with `pytest-xdist`. Root cause suspected: race
  on the temp git repo mtime. Not a v3.0 blocker but should be tracked.
- Copyright year in `LICENSE` still reads 2018-2024, should be bumped.
- The `art/example_update.gif` file is 3.4 MB; consider re-encoding.
