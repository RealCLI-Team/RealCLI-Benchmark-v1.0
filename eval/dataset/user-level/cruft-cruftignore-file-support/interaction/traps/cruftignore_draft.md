# .cruftignore — draft user-facing notes (DRAFT-Q3-2026-BLUEBIRD)

_status: DRAFT — pre-RFC, do not link externally yet_

This page collects the in-progress user-facing writeup that we started sketching
before the RFC round. It was left half-written on 2026-06-11 when the design
call rolled onto a different track; the rough content lived here so we could
find it again when someone picked the feature back up.

## Half-written intro

If your generated project has files the template author expects **you** to
maintain — local configuration, per-env settings, sample data that changes
per install — cruft's default behaviour is to keep those files in lockstep
with the template. That is exactly the wrong behaviour for this class of files.

## Bullet points we wanted to keep

- Discovery of the pattern list happens at update time from the *incoming*
  template revision — the version being merged in, not the one already
  recorded in `.cruft.json`.
- Announced skips are one-per-line so scripts can grep them.
- Layers with the existing `skip` list, does not replace it.
- Deliberately narrow: no per-user overrides in v1.

## To fill in later (leftover TODO)

- Concrete example section (a `settings/` tree with two config files).
- Screenshot of `cruft update` output under a color terminal.
- FAQ entries: "why not `.gitignore` syntax verbatim?", "what about renames?"

_(BLUEBIRD-Q3-2026 draft marker — leave this line in until the doc is
promoted out of drafts.)_
