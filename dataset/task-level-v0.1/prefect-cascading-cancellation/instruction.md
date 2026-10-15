# Make cancellation cascade to in-process nested subflows

We orchestrate data pipelines with Prefect 3: the top-level flow run is executed by the runner/executor in a separate subprocess (the most typical entry point is `prefect flow-run execute <flow-run-id>`; workers of process-type work pools go through the same path), and the flow code directly invokes nested subflows (in-process subflows, both sync and async). The repo is at `/app` (Prefect's source code).

Cancellation semantics currently have a nasty problem: after we cancel a running top-level run via the API/UI, the top-level run itself gets marked Cancelled, but **all nested subflow runs in the same process become Crashed**, and the `on_cancellation` hooks attached to both the parent flow and the subflows never run. Our resource cleanup and downstream notification logic hang off these hooks; treated as a crash, they never execute at all; the audit side also misreports a normal cancellation as a crash. On top of that, the execution process's exit posture after cancellation is not clean.

Make cancellation **cascading**: when a top-level run is cancelled, nested subflows in the same process must wind down with "cancellation" semantics instead of being treated as crashes. How you implement it internally is up to you (runtime mechanisms, how processes signal each other, etc. are all free design space), but the external behavior below must be strictly followed.

## Behavioral contract

Based on top-level runs executed by `prefect flow-run execute` (runner/worker subprocess scenarios are isomorphic):

- After a running top-level run is set to Cancelling: the execution process must exit cleanly (exit code 0), and the top-level run's final state is Cancelled.
- Nested subflow runs in the same process — sync and async subflows alike — must locally follow the `Cancelling -> Cancelled` path; the final state is Cancelled, not Crashed.
- The `on_cancellation` hooks of both the parent flow and the subflows must actually be invoked inside the execution process, and the flow_run each hook receives must correspond to its own run.
- User code currently blocking inside a subflow/parent flow (sleep, infinite loops, and the like) must be interrupted by this cancellation; it must not hang forever.

## Behavior that must not break

- A flow that finishes normally (including nested subflows) still ends Completed, with the execution process exiting 0; the Failed semantics of ordinary failures stay unchanged.
- If cancellation/termination arrives before the execution process is ready to handle it gracefully (e.g. the process just started, the engine hasn't entered the running state yet), keep the existing forceful-termination fallback: if it should wind down as a crash, wind it down as a crash. Graceful cancellation is not required at the instant of startup, and do not swallow external SIGTERM just to enable graceful cancellation.
- With `PREFECT_FLOW_RUN_EXECUTE_SIGTERM_BEHAVIOR=reschedule`, when the execution process receives SIGTERM it must still reschedule the run (AwaitingRetry); it must not be hijacked by the new cancellation/crash handling.
- The runner side's existing cancellation responsibilities (marking managed top-level runs Cancelled, running runner-side hooks) must remain functional; `prefect flow-run execute` and the process worker share the same cancellation path — don't fix one side and break the other.

## Verification

After the change, run a real end-to-end verification inside the container:

1. `prefect server start` to bring up a local server (the default sqlite is fine), and set `PREFECT_API_URL`.
2. Create a process-type work pool; write a flow with nested subflows where both parent and child have `on_cancellation` hooks (have the hooks drop a marker file to make confirmation easy), deploy it and create a run, and execute it with `prefect flow-run execute <id>`.
3. After both the parent and child runs enter Running, set the parent run to Cancelling via the API, and confirm: execution process exit code 0, parent run final state Cancelled, child run final state Cancelled, hooks at both levels executed (marker file contents correspond to their respective run ids). Verify once with a sync subflow and once with an async subflow.
4. Then confirm each item in the "must not break" section one by one, and run the repo's relevant tests (`tests/runner/`, `tests/test_flow_engine.py`, etc.) to confirm no regressions.
