# Fix chord failure leaving group body results pending forever and losing the exception chain

Our task orchestration service relies heavily on the canvas primitives in this codebase (`/app`, the Celery source). We use `chord` for "fan-in" aggregation: a group of tasks runs in parallel, and once all of them finish, their results are handed to the body for further processing. There has been a long-standing issue in production — please fix it thoroughly.

## Symptoms

When a chord's body is a `group`, the chord "dies half-finished" as soon as any task in the header fails:

- every task in the body group has an `AsyncResult` stuck in `PENDING` forever; callers' `get()` blocks until timeout, the whole group result never completes, and our monitoring only sees a pile of dangling results;
- the error callbacks attached to the body via `link_error` are not notified about each failed unit the way we expect;
- with the same failure, a body that is a plain single task behaves correctly (the errback fires, the result enters a failure state); it only hangs when the body is a group.

Another pain point for troubleshooting: when a chord fails, the `ChordError` seen by the consumer contains only a one-line text description. We want the error-handling code to have access to the original exception from the header task that actually failed (its type and arguments) so we can classify alerts and make retry decisions — currently this is completely impossible, because the exception information degrades to a plain string after a round trip through the result backend.

## Expectations

1. **Unified failure propagation semantics**: when a chord fails, no matter whether the body is a single task, a `chain`, or a `group`, any consumer waiting on its result must receive a definitive failure result within bounded time; no form of result may hang forever. When the body is a group, the result of every member task in the group must be retrievable in a failure state, the body's errback must fire for each failed unit, and the errback must be able to tell which member failed.
2. **Traceable exception chain**: the `ChordError` produced by a chord failure must allow tracing back to the original exception that triggered the failure via the standard exception chaining mechanism (the `raise ... from` machinery); this traceability must hold both inside the errback and after the result is read back from the result backend (given that the result serializer itself supports exception round-tripping). The scenario where the chord's body itself raises while executing (callback execution failure) must preserve the same traceability.
3. **Backward compatible, no breakage of existing behavior**:
   - when the entire header succeeds, the normal chord completion path (body is invoked, results passed in header order) must remain completely unchanged;
   - the failure-handling behavior for a single-task body (how many times the errback fires, how the result is marked) must not regress;
   - the existing text format of the failure message that retains information about the failed dependency task must not be lost;
   - a task that already has a `SUCCESS` result stored must not be overwritten by failure propagation (the result backend's dedup-protection semantics against duplicate writes must be preserved);
   - failure must not be propagated early before the whole header has returned; the retry semantics of `celery.chord_unlock` while the header is incomplete stay unchanged;
   - with a non-pickle serializer (e.g. JSON), behavior must not crash and states must stay correct.
4. **Full coverage**: the chord completion/failure paths of all built-in result backends (backends with native counting support and backends that rely on the built-in polling task) must behave consistently; do not patch just one path.

## Engineering requirements

- Changes should fit into the existing chord completion/failure handling paths and the implementations of the individual result backends; do not bypass the framework and build a parallel mechanism;
- Update the user documentation sections related to chord error handling;
- Add tests covering the behaviors above in the repository's test directory.

The repository is already installed in the environment in editable mode; after making changes you can run and verify directly.
