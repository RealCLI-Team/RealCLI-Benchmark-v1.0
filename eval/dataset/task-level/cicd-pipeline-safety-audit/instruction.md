## CI/CD Pipeline Safety Audit

You are a DevOps engineer at a company preparing for its first major production deployment. The team has written three GitHub Actions CI/CD pipeline configurations in `/app/pipelines/`. Your job is to build a **Pipeline Safety Analyzer** that inspects each workflow and produces a structured audit report.

### Requirements

Write `/app/analyze_pipelines.py` that:

1. Reads all `.yml` files from `/app/pipelines/`
2. Analyzes each workflow against these **production-readiness rules**:
   - **R1**: All action references must use a pinned SHA (40-char hex) or `@vN.M.P` tag, not `@main`, `@latest`, or `@dev`
   - **R2**: The workflow must include a job step that runs tests (step name or run command contains "test")
   - **R3**: Deploy-to-production jobs must depend on a staging deploy via `needs:` — no direct `prod` deploy without staging
   - **R4**: No hardcoded secrets in step-level `env:` blocks — flag every environment variable whose **name** contains `password`, `secret`, `token`, `api_key`/`api-key`, `access_key`/`access-key`, or `aws_secret` (case-insensitive) when its value is hardcoded plain text (values referencing `${{ secrets.* }}` are fine). Each offending variable counts as one violation.
   - **R5**: At least one notification step must exist (name or run contains "notify", "slack", "email", "teams", or "webhook")
   - **R6**: Deploy jobs must have resource limits (`cpus` or `memory` in `runs-on` options, or a container block with `options: --memory` / `--cpus`)
   - **R7**: `concurrency` must be defined at the workflow or job level to prevent parallel deploys
   - **R8**: `permissions` must be explicitly set (not relying on defaults)
3. Outputs `/app/report.json` with this schema:

```json
{
  "pipelines": {
    "<workflow-name>": {
      "safe": false,
      "score": 45,
      "violations": [
        {"rule": "R4", "detail": "Hardcoded secret found: DB_PASSWORD", "job": "deploy-prod", "step": 2}
      ]
    }
  }
}
```

### Scoring Formula

Each rule is worth **12.5 points** (8 rules × 12.5 = 100). A pipeline loses 12.5 points per violated rule.

- `safe` is `true` only if score >= 75 AND no R3 or R4 violations exist.
- `score` is an integer (round down).

### Example

For a pipeline violating R1, R4, and R7:
- Score = 100 - 12.5 × 3 = 62.5 → 62
- `safe` = false (score < 75)

Run your script: `cd /app && python analyze_pipelines.py`
Verify the output: `cat /app/report.json`
