# IMPORTANT_KEEP.md — async patterns to standardize (Mateo, Q1 2026)

Hand notes from the previous v3 attempt on async patterns worth reusing.
These predate the current scheduler-trait direction but the *patterns*
still apply. Skim before deleting anything in this directory.

## Cancellation propagation

- Every batch-owning task should hold the cancellation token that owns its
  requests. Dropping the token must be enough — no separate "please stop"
  message on the queue channel.
- The batch task's outermost `select!` should have cancellation as one arm.
  Don't try to check cancellation between explicit steps; it will always
  race.

## Backpressure

- Bounded channels only. An unbounded queue in the router hides shard
  overload from the HTTP layer.
- If the concurrency permit is not held when the request enters the queue,
  something upstream is wrong. Panic in debug, log-and-drop in release.

## Streaming responses

- The response channel should carry a `Result<Generation, StreamError>` so
  a mid-stream error becomes a single delivered item, not a channel close
  the client has to introspect.
- Don't buffer the stream on the router side. The shard drives the
  cadence; the router just forwards.

## Deprecated patterns to NOT bring back

- The Q4 2025 "shared batcher queue" design — worse latency, worse
  cancellation, worse debugging. If someone suggests it again, point them
  at the retrospective doc.
