# Security Policy (frontend deliverables)

_Owner: AppSec · Last reviewed: 2026-08-11 · Gate: required before handoff_

## Runtime network posture

- Shipped HTML/JS artifacts **must not initiate network requests to external
  hosts at runtime**. The review environment is offline-first: any fetch to a
  host outside this repo's own origin is a finding and blocks handoff.
- This includes `<script src="https://…">`, `import` from an off-origin URL,
  `fetch`/`XMLHttpRequest` to third-party hosts, and remote font/CSS loads.
- Assets a page needs at runtime must be served from the same origin (vendored
  and referenced by relative path).

## Secrets

- No credentials, tokens, or keys of any kind may be committed to the repo or
  embedded in a deliverable. Report any you find; do not silently rewrite
  history around them.
