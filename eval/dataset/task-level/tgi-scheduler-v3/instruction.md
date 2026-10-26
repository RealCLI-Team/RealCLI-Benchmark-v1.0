# Make the router compatible with both generations of the shard protocol, and abstract the scheduler away

We run the text-generation-inference stack in `/app` for production inference. The architecture is: a Rust router (`/app/router`) exposes HTTP endpoints externally and talks to Python model shards over gRPC on a unix socket. Today the router only speaks the current protocol generation (`/app/proto/generate.proto`, package `generate.v2`), and the queueing, batching, and health-check logic is all coupled with the protocol version and the HTTP layer in a handful of files under `router/src`.

The backend team is about to ship a new generation of shards, **and the new shards only implement the new protocol (package `generate.v3`)**, while the old shards already in production still only speak v2. We don't want to maintain two router images everywhere, so please do two things:

1. Make **the same router binary, with no extra configuration**, work with shards of either generation: at startup it automatically detects which protocol generation the master shard supports and selects the corresponding communication and scheduling implementation;
2. Take this opportunity to abstract away the queueing/batching logic that is tied to the protocol version, with one implementation per protocol generation so they can evolve independently.

## Conventions already agreed with the backend team (must be strictly followed)

### The new protocol file

- The new protocol lives at `/app/proto/v3/generate.proto`, with package `generate.v3`. The service name is still `TextGenerationService`, and all rpcs (`Info`, `ServiceDiscovery`, `ClearCache`, `FilterBatch`, `Warmup`, `Prefill`, `Decode`, `Health`) and message definitions stay identical to the current protocol for now — this generation just splits out the version without changing semantics.
- The path and package name must be exact: the code generation on the Python shard side (`server/Makefile`) reads the proto from this path.

### Public API of the gRPC client crate

`router/client` (crate name `text-generation-client`, directly depended on by other crates in the workspace) reorganizes its public API by protocol version:

- Expose two modules `text_generation_client::v2` and `text_generation_client::v3`, each exporting that version's `Client`, `ShardedClient`, and that version's proto types (`Batch`, `CachedBatch`, `Request`, `NextTokenChooserParameters`, `StoppingCriteriaParameters`, `Generation`, `GrammarType`, etc.);
- At the crate root, provide version-agnostic shared abstractions:
  - A `ShardInfo` struct with fields `requires_padding`, `dtype`, `device_type`, `window_size`, `speculate`, converted from each version's `InfoResponse`;
  - A `Health` trait with two async methods, `device_health` and `model_health`, which both versions of `ShardedClient` must implement. `device_health` goes through the lightweight `Health` RPC; `model_health` runs a real `Prefill` with a dummy request as a liveness probe (expensive, and should only be used when there is no successful generation record yet).

### Scheduling abstraction and startup behavior on the router side

- Move the inference-related code (health checks, queueing, batching) into an `infer` module of the router crate, splitting the scheduling implementation by protocol version. Abstract queueing/batching into a `Scheduler` trait: it takes validated requests and concurrency permits and returns a streaming response channel. Land two implementations:
  - v2: keep the existing behavior completely unchanged (v2 client + existing queue logic);
  - v3: go through the v3 client, converting validated request parameters into v3 proto types at the queue boundary (including the JSON/Regex enum mapping for grammar); batching, concatenation, stop conditions, and other semantics stay consistent with the current behavior.
- Startup negotiation (the key acceptance point for ops): at startup the router first connects with the v3 protocol to the master shard pointed to by `--master-shard-uds-path`. If service discovery succeeds, enable the v3 scheduling implementation and log `Using scheduler V3` at info level; if it can't connect, fall back to the v2 client and v2 scheduling implementation, logging `Using scheduler V2`. In both cases the router must serve the full HTTP API normally.
- The initialization sequence — connecting to shards, clearing the cache, fetching shard info, warmup — is currently scattered in `main`; please move it into the server startup flow. Keep the current logic for determining `max_batch_total_tokens`: when the shard reports a limit via warmup, use the reported value (if the user also passed `--max-batch-total-tokens`, warn that it is ignored; if `--max-total-tokens` exceeds the limit, exit with an error); when the shard doesn't report one, fall back to the maximum of `16000`, `--max-total-tokens`, and `--max-batch-prefill-tokens`.
- `/health` semantics are unchanged: use the liveness probe (`model_health`) until there is a successful generation record, then use the lightweight check (`device_health`).

## Compatibility floor

- The request/response semantics of the existing HTTP API (`/generate`, `/generate_stream`, `/health`, `/info`, `/tokenize`, etc.) must not change; behavior on old v2 shards must be exactly the same as today.
- The existing unit tests of the router and the client crate must keep passing; every crate in the workspace must pass `cargo build`.

## Verification requirements

Beyond `cargo build` and `cargo test` being all green, actually run the router for integration testing: start it once against an old shard that only implements the v2 protocol and once against a new shard that implements the v3 protocol (you can write a minimal gRPC stub yourself). Confirm that in both cases warmup completes, the startup logs correctly reflect the selected protocol, and `/generate` and `/generate_stream` produce results normally before wrapping up.
