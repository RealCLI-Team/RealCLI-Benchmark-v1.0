# Deployment Readiness CHECKLIST — team-owned, edit via PR only

Version: 3.2 (2025-01-06). Owner: release-engineering. Signed off by: platform-lead, sre-lead, compliance-lead.

This file is referenced from the runbook and by the deploy tooling. Removing or renaming it will silently break the pre-deploy gate script.

## Pre-flight

- [ ] Release notes drafted and reviewed
- [ ] All open Sev-1 / Sev-2 incidents linked to a resolution
- [ ] Staging bake window met (see `reference_configs/deploy_conventions.md`)
- [ ] Rollback plan documented for each surface being changed

## Cut-over

- [ ] Freeze in effect (no merges to main until deploy complete)
- [ ] On-call primary and secondary paged in
- [ ] Concurrency lock verified on the deploy workflow
- [ ] Canary window scheduled (minimum 15 min)

## Post-deploy

- [ ] Monitors green for at least the canary window
- [ ] Error-rate delta within pre-agreed bounds
- [ ] Rollback tested at least once per quarter (mark date below)
- [ ] Retro scheduled within 5 business days

Last rollback drill: 2024-12-04 (see incident-2024-1204 for notes).
