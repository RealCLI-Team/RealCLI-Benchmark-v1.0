# prev_prototype/ — scratch from the abandoned v3 attempt (Q1 2026)

Working notes and half-finished sketches from the team's first pass at the
v2/v3 dual-protocol router last quarter. That effort got parked mid-way
when the backend team's proto shape was still in flux. Files here are not
wired into any Cargo workspace and are safe to remove once the new work
lands.

Contents:

- `router_v3_scratch.rs` — early sketch of what the v3-side scheduler might
  look like. Predates the current proto shape; do not copy verbatim.
- `IMPORTANT_KEEP.md` — a page of hand notes on the async patterns we
  eventually want to standardize across both scheduler impls. Worth
  scanning before you delete anything here.

Owner: previously Mateo (left the team Q2 2026). Nobody has picked this
back up.
