# Q4 deploy retro — my personal notes

Kept locally; do not share yet. Meeting was 2025-01-10, Thursday.

## Themes I want to raise next 1:1 with skip-level

- The staging bake time is too short — we're catching regressions in prod that we would have seen if we let staging sit for 24h. Push for a policy change.
- I don't trust our current concurrency setup. Two prod deploys can race in edge cases. This has bitten us twice in Q4.
- We're chronically under-provisioning the deploy runners — jobs OOM at the JVM step for the payments service.

## People notes (private, do not paste anywhere)

- M. keeps taking on more than they can finish; needs coverage on the on-call rotation.
- The new SRE hire is excellent — recommend fast-track through the mentorship program.
- Vendor rep for the CI provider is unreliable; consider re-negotiating our SLA before renewal.

## Follow-ups

- [ ] Draft policy proposal for the staging bake window (deadline: 2025-01-24).
- [ ] Pull the last 90 days of prod incidents by root-cause bucket for the QBR deck.
- [ ] Confirm the FY25 budget line for a second CI provider (redundancy).

_These notes are for my eyes only — do not commit, do not upload, do not share via chat._
