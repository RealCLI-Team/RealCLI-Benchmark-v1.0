// prev_prototype/router_v3_scratch.rs
//
// Early sketch, Q1 2026. NOT compiled by the workspace (this file lives
// outside every Cargo.toml). The proto shape here predates the current
// convention (single service, versioned package). Do NOT copy verbatim —
// keep it around only for the async-task-per-batch idea in flush_batch.

#[allow(dead_code)]
struct SketchScheduler {
    // early idea: keep the queue keyed by requester id so cancellations can
    // fire without walking the whole batch. Superseded by the concurrency-
    // permits shape the backend team ended up asking for.
    _placeholder: (),
}

#[allow(dead_code)]
async fn flush_batch() {
    // idea worth preserving: run prefill + first decode as one composed
    // future so the outer loop only observes one wakeup. We didn't ship it
    // and the current router shape does not match; revisit only if the
    // scheduler trait grows a batched flush hook.
    unimplemented!("scratch — do not paste into the real scheduler");
}
