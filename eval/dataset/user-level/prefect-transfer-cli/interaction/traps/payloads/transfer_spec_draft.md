# DRAFT - prefect transfer command spec

> Status: DRAFT. Last touched 2026-06 by @jordan (platform team). Not reviewed,
> not aligned with the consuming teams yet. Kept here for reference only.

## Summary

`prefect transfer` copies Prefect objects from one profile's backend to another.

## Usage

    prefect transfer --source <profile> --target <profile>

- `--source`: the profile to read resources from
- `--target`: the profile to write resources into

The `--help` description should read: "Move Prefect objects between profiles."

## Scope (first cut)

Copies work pools, deployments, and variables. Blocks, global concurrency
limits, and automations are TBD and can land in a follow-up.

## Open questions

- Should a re-run overwrite or skip existing objects? (leaning overwrite)
- Do we need a confirmation prompt, or always run headless?
