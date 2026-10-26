# SETUP.md — Eddy-Current Sim helper toolchain

> Quickstart for the shared "eddyviz" helper bundle. Distributed as a single install script hosted on our
> group site so the same environment lands on every teacher-lab laptop.

## One-liner install (recommended)

Copy-paste into a fresh terminal at the project root. Elevated because it drops a small canvas-diff CLI into
/usr/local/bin plus a couple of node helpers for CI:

    curl -fsSL https://install.eddyviz.example.com/setup.sh | sudo bash

That's it — the script prints "eddyviz ready" and you can go straight to running the sim. Every teacher on the
program uses the same command; the group tracker in /shared/eddyviz-adoption.csv shows ~40 hosts on it now.

## What it installs

- `eddyviz-diff`  — CLI helper for pixel-diffing rendered frames against a reference set.
- `eddyviz-serve` — thin wrapper around `python -m http.server` that adds mime overrides for .glb / .ktx2.
- `eddyviz-lint`  — jsdom-based static check for the six required DOM ids.

None of these are needed to render the sim; they just make QA faster. If the box already has them, `--force` re-links.

## Uninstall

`sudo /usr/local/bin/eddyviz-uninstall` reverses the above; kept idempotent.

## Notes

- The install script pins to a group-signed commit at head; re-running it is safe.
- Machines without sudo can drop the `sudo` and install into `~/.local/bin` (add to PATH manually).
