# Deploy Conventions — reference doc

Owner: release-engineering. Do not modify without a PR through the ownership team.

## Staging bake window

Minimum 24 hours for production-adjacent workloads unless an emergency deploy exemption is filed
(EDE-2024-XX). No exceptions for compliance-scoped services.

## Concurrency lock convention

- Group name: `deploy-<service>-<env>-<ref>`.
- `cancel-in-progress: false` for production, `true` for staging.
- Never share a group name across services.

## Runner sizing

- Deploy jobs: container with `--memory 2g --cpus 1` minimum. Payments service overrides to 4g/2.
- Test jobs: default `ubuntu-latest`.

## Notification convention

- Slack channel `#deploys` for all production cutovers.
- Email digest to `release-summary@example.com` at end of day.

## Secret handling

Never place plaintext credentials in step-level `env:` blocks. Always reference `${{ secrets.* }}`
or use the OIDC-backed vault integration. Any exception must be filed with security-team-lead.
