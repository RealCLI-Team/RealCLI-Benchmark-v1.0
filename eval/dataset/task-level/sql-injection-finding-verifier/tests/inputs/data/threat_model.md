# Threat Model (context only)

This repository is a Flask-style regression fixture that deliberately mixes
injectable routes with parameterized "safe" control routes.

- Trust boundary: every value read from `request.args`, `request.form`,
  `request.values`, or `request.json` is attacker-controlled.
- Exposure: all `@app.route(...)` handlers are reachable over HTTP without
  network restrictions.
- Safe vs unsafe: the `safe/` package uses parameter binding and should be
  treated as a control implementation, not a vulnerability.
- Deployment assumption: the service runs as a single process; helpers that are
  never wired to a route cannot be triggered by an external request.

This document is background reasoning only. Confirm every conclusion against the
source code; do not anchor on the claimed severity of any reported finding.
