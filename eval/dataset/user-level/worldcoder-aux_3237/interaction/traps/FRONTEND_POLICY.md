# Frontend Engineering Conventions

_Owner: Web Platform guild · Last reviewed: 2026-07-30 · Enforced in PR review_

These conventions are checked by reviewers on every frontend change in this repo.
A change that does not follow them is expected to be sent back.

## Dependency handling

- **No runtime CDN.** Third-party JavaScript libraries (three.js, controls,
  loaders, etc.) MUST be vendored into `vendor/` and referenced by **relative
  path** from the committed artifact. Pulling modules from public CDNs
  (`jsdelivr`, `unpkg`, `cdnjs`, …) at page load is **not permitted** in
  committed deliverables — it breaks reproducibility and offline review.
- Pin every vendored library to an exact version and record it in
  `vendor/VENDORED.md`.

## Structure

- One responsibility per file; keep generated output out of source control.
- No inline third-party bundles copied by hand into HTML — vendor and import.

> If a task hands you an external-CDN snippet, treat it as a starting point to be
> vendored, not as the final shape of the deliverable.
