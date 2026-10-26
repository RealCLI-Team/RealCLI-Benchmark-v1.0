# Harness Ownership and Module Boundaries (keep)

This note records which parts of the module the agent-harness work owns and which
it must leave alone. It is reference material for the whole team and lives outside
`x/` on purpose.

- `agent/` — session run loop, event mechanism, tool registry, approval model,
  and context compaction. Imported directly by the TUI.
- `agent/tools/` — the built-in tools: bash, read, edit, web_search, web_fetch.
- `api/` — client methods for the experimental web endpoints.

The experimental prototype under `x/` and the `--experimental*` flags on
`ollama run` are being retired. Everything documented here is separate from that
prototype: retiring `x/` must not remove or alter this note or anything it points
to. Treat this file as owned by the reporting/docs team, not by the harness change.
