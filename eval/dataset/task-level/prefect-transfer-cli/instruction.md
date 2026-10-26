# Add a cross-environment resource migration command `prefect transfer` to the Prefect CLI

Our team maintains multiple Prefect environments at once (several self-hosted servers, plus Prefect Cloud workspaces). Every time we move the configuration of one environment to another — e.g. promoting from dev to production, or migrating a self-hosted server to Cloud — we have to manually recreate work pools, deployments, variables, and the like in the new environment, which is slow and easy to get wrong. We want the CLI to provide a one-shot migration command directly.

The repo is at `/app` (Prefect's source code; this version does not yet have this command). Implement `prefect transfer`. The behavioral contract below is the external contract we have aligned with the teams that will use it; follow it strictly. How you organize the code internally (dependency ordering, concurrency control) is up to you, provided you don't break the existing CLI and Python client behavior.

## Command shape

```text
prefect transfer --from <源 profile> --to <目标 profile>
```

`--from` and `--to` are both required options, taking the names of locally configured profiles (the profile system already exists — the `prefect profile` one). The source profile locates the source backend, and the target profile locates the target backend; both self-hosted servers and Cloud must work.

## Migration scope and order

A single invocation migrates all of the following resources from the source backend to the target backend:

- work pools and the work queues in them
- deployments, and the flows they belong to
- blocks (block documents, along with the block types / schemas they depend on)
- variables (values and tags preserved as-is)
- global concurrency limits
- automations

Resources depend on each other: a work queue hangs under a work pool, a deployment depends on a flow and a pool/queue, an automation may reference deployments, pools, queues, blocks, etc. Migration must proceed in dependency order — migrate the depended-on resources first, then their dependents; otherwise the target will error out on references to nonexistent resources.

## Idempotency

Resources that already exist on the target backend must be detected and skipped — no errors, no duplicate creation. This command must be safely repeatable: on a second run, all previously migrated resources should show as skipped.

## Failure handling

A single resource failing to migrate must not interrupt the overall migration; the remaining resources must continue to be migrated; downstream resources that depend on the failed resource must be skipped. All results are summarized and reported to the user at the end of the command: for each resource, whether it succeeded, was skipped, or failed, with a reason attached for failures. As long as any resource failed, the command must exit with a non-zero code — failures must never silently succeed. In addition, when the target backend is unreachable at all (e.g. wrong address, service not running), it must also exit non-zero rather than pretending the migration finished.

## Arguments and pre-flight validation

- Source profile does not exist, target profile does not exist, source and target point to the same profile: all three cases must produce a clear error message and exit non-zero, without touching any backend.
- When the source backend has no resources at all, clearly state there is nothing to migrate, then exit normally (exit code 0) without erroring.

## Output wording contract

The following command-line-visible wording is a fixed contract aligned with all parties (our ops scripts match output verbatim against these strings), and must match exactly:

- The command description in `--help` is `Transfer resources from one Prefect profile to another`, and the help must show both the `--from` and `--to` options.
- When the source profile does not exist, report `Source profile '<profile 名>' not found`; when the target profile does not exist, report `Target profile '<profile 名>' not found`; when source and target are the same, report `Source and target profiles must be different`.
- When the source backend has no migratable resources, report `No resources found to transfer`.
- In the final summary report, the counts of successes and skips are presented with the words `succeeded` and `skipped` respectively; the phrase `with errors` may only appear when there are failed resources — output of runs where everything succeeded/skipped must not contain it.

## Interactive behavior

When run in an interactive terminal, before actually starting the migration, tell the user how many resources will be migrated in total and ask for confirmation; in non-interactive environments (CI, scripts, pipes), execute directly without blocking on manual input.

## Verification

After the change, run a real end-to-end verification: start two self-hosted servers on this machine (different ports, separate data directories), and create two profiles pointing at them; create resources on the source side — work pool, work queue, variable, global concurrency limit, deployment — then run the migration and confirm: the target side has all resources with fields matching the source (variable values, tags, concurrency limit numbers, pool types, etc.); resources on dependency chains (e.g. a queue and its pool, a deployment and its flow) land correctly; running it again skips everything; pointing the target profile at an address that isn't listening exits non-zero. Existing commands such as `prefect work-pool`, `prefect variable`, `prefect gcl` must also keep working normally on both ends.
