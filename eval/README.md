# REALCLI Evaluation

Evaluation harness and dataset for **REALCLI**, a paired benchmark for CLI agents.
Both tracks use the same 101 task instances, container environments, and outcome
verifiers:

- **REALCLI-EXEC** (task-level): the complete task specification is available up
  front; the agent works autonomously.
- **REALCLI-COLLAB** (user-level): a scripted user releases requirements
  progressively and injects collaboration probes during execution. The
  collaboration dimensions are described in `dataset/user-level/domains.md`.

## Directory layout

```
eval/
├── harbor/                      # maintained Harbor 0.24.0 (COLLAB track)
└── dataset/
    ├── task-level/              # 101 EXEC instances
    │   ├── <task>/
    │   │   ├── instruction.md
    │   │   ├── task.toml
    │   │   ├── environment/Dockerfile
    │   │   ├── tests/
    │   │   └── solution/
    │   ├── SOURCES.json         # upstream repositories and pinned commits
    │   └── clone_repos.py       # materialize repositories for offline use
    └── user-level/              # 101 COLLAB instances
        ├── domains.md           # collaboration dimension taxonomy
        └── <task>/
            ├── instruction.md
            ├── task.toml
            ├── environment/Dockerfile
            ├── tests/
            ├── solution/
            └── interaction/     # persona, information slices, probes, traps
```

## Requirements

- Docker 24+ (local daemon, or a remote daemon via `DOCKER_HOST`)
- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Credentials for the model under test (for example `ANTHROPIC_API_KEY`;
  provider-specific variables can also be passed with `--ae NAME=VALUE`)

## EXEC track (task-level): upstream Harbor

Task-level evaluation uses the official Harbor release:
<https://github.com/harbor-framework/harbor>.

```bash
uv tool install harbor        # or: pip install harbor

harbor run \
  -p eval/dataset/task-level \
  -a claude-code -m <model> \
  -n 8 --yes
```

`task.toml` points at prebuilt images on Docker Hub, so Harbor pulls instead of
building. To rebuild everything from the public Dockerfiles:

```bash
harbor run -p eval/dataset/task-level -a claude-code -m <model> -n 8 --yes --force-build
```

Notes:

- 40 task-level Dockerfiles clone pinned upstream repositories from GitHub at
  build time. Without `--force-build` the prebuilt images are used and no
  build-time network access is required.
- `SOURCES.json` records every upstream repository and commit;
  `clone_repos.py` materializes them for offline inspection.
- The html / ppt / word tasks use an LLM judge in their verifier and require
  `JUDGE_API_BASE` and `JUDGE_API_KEY` (OpenAI-compatible endpoint with `/v1`).

Oracle calibration:

```bash
harbor run -p eval/dataset/task-level -a oracle -n 12 --yes
```

## COLLAB track (user-level): maintained Harbor

The user-level track needs the `harbor/` codebase in this directory. It is
Harbor 0.24.0 plus the scripted user simulator and the interaction-dimension
judge, which are not part of the upstream release.

```bash
cd eval/harbor
uv sync --all-extras

uv run harbor run \
  -p ../dataset/user-level \
  -a harbor.agents.installed.claude_code_usersim:ClaudeCodeUsersim \
  -m <model> -n 8 --yes \
  --agent-max-timeout-sec 1800
```

The simulator reads `interaction/script.json` for each task and releases the
information slices turn by turn, driving the assigned probes. User-level
`task.toml` files enable it with:

```toml
[user_simulation]
enabled = true
script_path = "interaction/script.json"
max_turns = 60
max_tokens = 32768
```

Oracle calibration (single-turn; the oracle agent disables user simulation):

```bash
uv run harbor run -p ../dataset/user-level -a oracle -n 8 --yes
```

### Interaction judge (`Content_Score`)

After the verifier, Harbor runs a best-effort interaction judge that scores the
collaboration checklist items and writes `judge_result` (the `Content_Score`
column in the result table). A judge failure never changes the task reward.

The judge needs a Claude Code CLI on the host:

```bash
export HARBOR_JUDGE_CLAUDE_BIN=/path/to/claude
# optional: route the judge through an Anthropic-compatible endpoint
export HARBOR_JUDGE_BASE_URL=...
export HARBOR_JUDGE_API_KEY=...
```

The dimension definitions live in `dataset/user-level/domains.md` and are found
automatically by walking up from each task directory.

## Prebuilt images

The default image references are public:

```
bingliwang/clibench-env-task-level:<task>
bingliwang/clibench-env-user-level:<task>
bingliwang/clibench-verifier-task-level:<task>
bingliwang/clibench-verifier-user-level:<task>
```

Docker Hub rate limits apply: 100 pulls per 6 hours anonymously, 200 when logged
in. A full sweep pulls 202 environment images and 101 verifier images, so run
`docker login` first or use `--force-build`.

## Dataset format

Each instance is self-contained. `task.toml` declares timeouts, resources, the
environment image or Dockerfile, and verifier configuration. `tests/` writes the
task reward to `/logs/verifier/reward.txt` (or `reward.json`). `interaction/`
exists only for COLLAB instances and contains the scripted user persona,
information slices, probes, hard checks, and checklist items.

## Verification

- 202 environment images and 101 verifier images build successfully from the
  public Dockerfiles, and the published images pull cleanly.
- Oracle calibration on task-level: 91/101 pass without judge credentials; the
  10 html/ppt/word instances require `JUDGE_API_BASE` / `JUDGE_API_KEY`.
- Oracle calibration on a 10-instance user-level sample: 10/10 pass.
- Interaction judge prompts assemble for all 101 user-level instances.

## Citation

See the repository root README for the benchmark description and citation.
