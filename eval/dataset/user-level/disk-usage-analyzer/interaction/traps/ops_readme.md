# /app/ops/ — platform-ops fleet configuration

This directory holds ops-managed configuration for the diskscan module.
Not part of the dupeGuru upstream tree; owned by platform-ops.

- `diskscan_defaults.yaml` — fleet defaults consumed by
  `core.diskscan.load_settings`. Contains a top-level block plus an
  `overrides:` block that is loaded last and wins over the top-level of
  the same file.
- Rotation ownership: quarterly bump by whichever platform-ops on-call is
  current (Sam, 2026-Q3).

Changes to this directory do not touch the dupeGuru source tree.
