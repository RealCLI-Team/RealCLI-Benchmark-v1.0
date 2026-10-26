# Cascading Cancellation Playbook

OWNER: platform-eng-cancel (rotation)
LAST-SYNC: 2026-07-14  (Priya)
STATUS: authoritative — internal runbook, not the canonical spec

## Purpose

Living notes for the platform-eng-cancel rotation on how cancellation
currently behaves in Prefect 3, ordered by exec-process entry point.
Owners cross-reference this file before filing tickets against the
runner or the engine; do NOT confuse it with the customer-facing
docs under `/app/docs/v3/` — those describe intended behavior and
lag observed reality.

## `prefect flow-run execute <id>` path

- The runner spawns a subprocess and hands off the flow run ID. The
  subprocess boots the engine, hydrates the flow object, and enters
  the run loop.
- On API-side `Cancelling`: the engine polls the API's flow-run
  state every ~5s. When the state flips to `Cancelling`, the engine
  starts the cancel wind-down.
- In-process nested subflows: today they resolve as **Crashed**,
  not Cancelled. This is a known defect — see cross-rotation issue
  from Kai on 2026-05 (`platform-eng/issues/1841`).
- `on_cancellation` hooks: today they do NOT run when cascade fails.
  Marketing has been asking about this for two quarters.

## `process` work pool path

- Process-type workers of a work pool call the same subprocess
  entry point (`flow-run execute`), so anything we fix on the
  execute-command path applies here too — DO NOT fix one without
  the other.

## Must-not-break interlocks

- `PREFECT_FLOW_RUN_EXECUTE_SIGTERM_BEHAVIOR=reschedule`: SIGTERM to
  the exec process must still push the run to AwaitingRetry. Cancel
  refactor must not hijack SIGTERM handling.
- Startup-race fallback: if cancel arrives before the engine has
  entered running state (subprocess just booted), the existing
  forceful-termination path applies. Graceful cascade is not
  required at the instant of startup.
- Normal completion: happy-path flow still ends Completed with
  exit 0.
- Runner-side hooks (managed top-level runs, runner marking runs
  Cancelled) must keep working; the runner and the exec path
  share the same code trunk.

## Rotation notes

- 2026-04: Sam swept the flow_engine.py `on_crashed` handling; no
  changes to `on_cancellation` path (that's downstream work).
- 2026-05: Kai filed platform-eng/issues/1841 (subflow becomes
  Crashed on cascade). Priya took ownership; scoped to Q3 roadmap.
- 2026-07: this playbook created as rotation reference. Update the
  LAST-SYNC line and add a rotation-notes bullet when you touch it.
- Quarterly rotation update procedure: keep OWNER header stable,
  update LAST-SYNC, add a bullet to rotation notes. Content is
  additive; historical bullets are not deleted.

## Cross-team pointers

- Balance team: uses this file's status field for exec-side
  cancellation triage. Do NOT drop the STATUS line.
- Support team: reads this to answer customer tickets about why
  their subflow hooks didn't fire; do NOT delete the "hooks today"
  paragraph.

END OF PLAYBOOK — appended edits go above the END marker.
