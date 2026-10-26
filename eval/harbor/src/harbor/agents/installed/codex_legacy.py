"""Dormant Harbor-native Codex implementation kept outside the public adapter."""

import json
import shlex
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar, Literal, override

from harbor.agents.installed.base import (
    BaseInstalledAgent,
    CliFlag,
    NonZeroAgentExitCodeError,
    with_prompt_template,
)
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.agent.name import AgentName
from harbor.models.trajectories import (
    Agent,
    FinalMetrics,
    Metrics,
    Observation,
    ObservationResult,
    Step,
    ToolCall,
    Trajectory,
)
from harbor.models.trial.paths import EnvironmentPaths
from harbor.utils.env import parse_bool_env_value
from harbor.utils.trajectory_utils import format_trajectory_json


class _LegacyCodex(BaseInstalledAgent):
    """
    The Codex agent uses OpenAI's Codex CLI tool to solve tasks.
    """

    SUPPORTS_ATIF: bool = True
    SUPPORTS_RESUME: bool = True
    _OUTPUT_FILENAME = "codex.txt"
    _REMOTE_CODEX_HOME = PurePosixPath("/tmp/codex-home")
    _REMOTE_CODEX_SECRETS_DIR = PurePosixPath("/tmp/codex-secrets")
    _INSTALL_CHECK_COMMAND = (
        "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; "
        "command -v codex >/dev/null 2>&1"
    )
    _INSTALL_VERSION_COMMAND = (
        "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; codex --version"
    )

    def __init__(
        self,
        *args,
        openai_api_key: str | None = None,
        openai_base_url: str | None = None,
        mounted_runtime_path: str | None = None,
        **kwargs,
    ):
        if mounted_runtime_path is None:
            mounted_runtime_path = kwargs.pop("mounted-runtime-path", None)
        else:
            kwargs.pop("mounted-runtime-path", None)
        super().__init__(*args, **kwargs)
        self._openai_api_key = openai_api_key
        self._openai_base_url = openai_base_url
        self._mounted_runtime_path = mounted_runtime_path

    # No per-flag whitelist. Reasoning (and every other codex config key) is
    # passed via config.toml through HARBOR_CODEX_EXTRA_CONFIG_TOML — one uniform
    # channel, no per-key CliFlag to maintain. (`--ak` only ever mapped these two
    # hardcoded keys and silently dropped anything else, which was confusing.)
    # model_reasoning_effort / model_reasoning_summary are valid top-level
    # config.toml keys (verified), so e.g.:
    #   HARBOR_CODEX_EXTRA_CONFIG_TOML=$'model_reasoning_effort = "low"\n...'
    # Upstream v0.16.0 added web_search / reasoning_* CliFlags; those config keys
    # remain reachable through the same HARBOR_CODEX_EXTRA_CONFIG_TOML channel.
    CLI_FLAGS: ClassVar[list[CliFlag]] = []

    @staticmethod
    @override
    def name() -> str:
        return AgentName.CODEX.value

    @property
    def _trajectory_path(self) -> PurePosixPath:
        return PurePosixPath(EnvironmentPaths.agent_dir / "trajectory.json")

    @override
    def get_version_command(self) -> str | None:
        return "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; codex --version"

    @override
    def parse_version(self, stdout: str) -> str:
        text = stdout.strip()
        for line in text.splitlines():
            line = line.strip()
            if line:
                return line.removeprefix("codex-cli").strip()
        return text

    async def _installed_codex_satisfies_version(
        self, environment: BaseEnvironment
    ) -> bool:
        if self._version is None:
            check_result = await environment.exec(command=self._INSTALL_CHECK_COMMAND)
            return check_result.return_code == 0

        version_result = await environment.exec(command=self._INSTALL_VERSION_COMMAND)
        if version_result.return_code != 0:
            return False

        installed_version = self.parse_version(version_result.stdout or "")
        return installed_version == self._version

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        if await self._installed_codex_satisfies_version(environment):
            self.logger.debug("Codex is already available at the requested version")
            return

        # Opt-in: a self-contained local codex binary uploaded into the
        # container (same model as HARBOR_LOCAL_OPENCODE_BINARY). codex 0.142
        # ships a single self-sufficient native binary (ripgrep / code-mode-host
        # embedded), so we upload it directly and skip the mounted-runtime / npm
        # install. Works on any backend that supports upload_file (docker,
        # remote sandbox, ...), avoiding online npm/nvm at high concurrency.
        local_binary = (self._get_env("HARBOR_LOCAL_CODEX_BINARY") or "").strip()
        if local_binary:
            if not Path(local_binary).is_file():
                raise FileNotFoundError(
                    f"HARBOR_LOCAL_CODEX_BINARY does not exist: {local_binary}"
                )
            remote_binary = "/tmp/harbor-codex"
            await environment.upload_file(local_binary, remote_binary)
            quoted_binary = shlex.quote(remote_binary)
            # Install onto the default PATH so run()'s bare `codex` resolves.
            await self.exec_as_root(
                environment,
                command=(
                    "set -euo pipefail; "
                    f"if [ ! -r {quoted_binary} ]; then "
                    "echo 'Codex binary is not readable.' >&2; "
                    f"echo {quoted_binary} >&2; exit 1; fi; "
                    f"cp {quoted_binary} /usr/local/bin/codex; "
                    "chmod 0755 /usr/local/bin/codex; "
                    f"rm -f {quoted_binary}; "
                    "codex --version"
                ),
            )
            # Give the agent user a writable ~/.local/bin (codex extracts helper
            # binaries there on demand) and a same-name shim on the user PATH.
            await self.exec_as_agent(
                environment,
                command=(
                    'mkdir -p "$HOME/.local/bin"; '
                    'ln -sf /usr/local/bin/codex "$HOME/.local/bin/codex"; '
                    'export PATH="$HOME/.local/bin:$PATH"; codex --version'
                ),
            )
            return

        # Opt-in: prefer a pre-seeded, bind-mounted runtime (node + codex + rg)
        # over an in-container package install. It keeps every host identical,
        # works offline, and avoids version drift. Enabled only when the runtime
        # is configured via mounted_runtime_path or HARBOR_CODEX_MOUNTED_RUNTIME;
        # otherwise the default install below is used unchanged.
        mounted_runtime_path = self._mounted_runtime_path or self._get_env(
            "HARBOR_CODEX_MOUNTED_RUNTIME"
        )
        if mounted_runtime_path:
            try:
                await self.exec_as_agent(
                    environment,
                    command=(
                        "set -euo pipefail; "
                        f"RUNTIME={shlex.quote(mounted_runtime_path)}; "
                        'NODE_BIN="$RUNTIME/node/bin/node"; '
                        'CODEX_BIN="$RUNTIME/bin/codex"; '
                        'RG_BIN="$RUNTIME/bin/rg"; '
                        '[ -x "$NODE_BIN" ] && [ -x "$CODEX_BIN" ]; '
                        'mkdir -p "$HOME/.local/bin"; '
                        'ln -sf "$CODEX_BIN" "$HOME/.local/bin/codex"; '
                        'if [ -x "$RG_BIN" ]; then ln -sf "$RG_BIN" "$HOME/.local/bin/rg"; fi; '
                        'export PATH="$HOME/.local/bin:$RUNTIME/node/bin:$PATH"; '
                        # At scale, many concurrent `docker compose up` + immediate
                        # exec can leave the just-created overlay rootfs momentarily
                        # settling, so execve of the `#!/bin/sh` codex wrapper
                        # transiently returns ENOENT -> "codex: command not found"
                        # (exit 127). Retry to absorb it.
                        "for _i in $(seq 1 30); do "
                        "codex --version >/dev/null 2>&1 && node --version >/dev/null 2>&1 && break; "
                        "sleep 2; done; "
                        "codex --version && node --version && "
                        "printf 'NODE_BIN=%s\\nCODEX_BIN=%s\\nRG_BIN=%s\\n' "
                        '"$NODE_BIN" "$CODEX_BIN" "$RG_BIN" > /tmp/harbor-codex-bin-paths.env'
                    ),
                )
                await self.exec_as_root(
                    environment,
                    command=(
                        "set -e; "
                        "test -f /tmp/harbor-codex-bin-paths.env; "
                        ". /tmp/harbor-codex-bin-paths.env; "
                        '[ -n "${NODE_BIN:-}" ] && [ -x "$NODE_BIN" ]; '
                        '[ -n "${CODEX_BIN:-}" ] && [ -x "$CODEX_BIN" ]; '
                        'ln -sf "$NODE_BIN" /usr/local/bin/node; '
                        'ln -sf "$CODEX_BIN" /usr/local/bin/codex; '
                        'if [ -n "${RG_BIN:-}" ] && [ -x "$RG_BIN" ]; then '
                        '  ln -sf "$RG_BIN" /usr/local/bin/rg; '
                        "fi; "
                        "command -v node >/dev/null && command -v codex >/dev/null"
                    ),
                )
                return
            except NonZeroAgentExitCodeError:
                # If the mounted runtime is required, surface the failure;
                # otherwise fall through to the package install below.
                if parse_bool_env_value(
                    self._get_env("HARBOR_CODEX_REQUIRE_MOUNTED_RUNTIME") or "0"
                ):
                    raise

        # Install system packages (root)
        await self.exec_as_root(
            environment,
            command=(
                "if ldd --version 2>&1 | grep -qi musl || [ -f /etc/alpine-release ]; then"
                "  apk add --no-cache curl bash nodejs npm ripgrep;"
                " elif command -v apt-get &>/dev/null; then"
                "  apt-get update && apt-get install -y curl ripgrep;"
                " elif command -v yum &>/dev/null; then"
                "  yum install -y curl ripgrep;"
                " else"
                '  echo "Warning: No known package manager found, assuming curl is available" >&2;'
                " fi"
            ),
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        # Install codex (as default user)
        version_spec = f"@{self._version}" if self._version else "@latest"
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                "if ldd --version 2>&1 | grep -qi musl || [ -f /etc/alpine-release ]; then"
                f"  npm install -g @openai/codex{version_spec};"
                " else"
                "  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.2/install.sh | bash &&"
                '  export NVM_DIR="$HOME/.nvm" &&'
                '  \\. "$NVM_DIR/nvm.sh" || true &&'
                "  command -v nvm &>/dev/null || { echo 'Error: NVM failed to load' >&2; exit 1; } &&"
                "  nvm install 22 && nvm alias default 22 && npm -v &&"
                f"  npm install -g @openai/codex{version_spec};"
                " fi && "
                "codex --version"
            ),
            env={"NVM_NODEJS_ORG_MIRROR": "https://nodejs.org/dist"},
        )
        # Symlink node/codex to /usr/local/bin (root)
        await self.exec_as_root(
            environment,
            command=(
                "for bin in node codex; do"
                '  BIN_PATH="$(which "$bin" 2>/dev/null || true)";'
                '  if [ -n "$BIN_PATH" ] && [ "$BIN_PATH" != "/usr/local/bin/$bin" ]; then'
                '    ln -sf "$BIN_PATH" "/usr/local/bin/$bin";'
                "  fi;"
                " done"
            ),
        )

    def _get_session_dir(self) -> Path | None:
        """Get the single session directory."""
        sessions_dir = self.logs_dir / "sessions"
        if not sessions_dir.exists():
            return None

        session_dirs = [d for d in sessions_dir.rglob("*") if d.is_dir()]
        if not session_dirs:
            return None
        max_depth = max(len(d.parts) for d in session_dirs)
        session_dirs = [d for d in session_dirs if len(d.parts) == max_depth]
        if not session_dirs:
            return None

        # Sanity check: there should be exactly one session
        if len(session_dirs) != 1:
            raise ValueError(f"Expected exactly 1 session, found {len(session_dirs)}")
        return session_dirs[0]

    @staticmethod
    def _extract_message_text(content: list[Any]) -> str:
        """Extract joined text from Codex content blocks."""
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)

    @staticmethod
    def _parse_output_blob(raw: Any) -> tuple[str | None, dict[str, Any] | None]:
        """Extract textual output and metadata from Codex tool outputs."""
        if raw is None:
            return None, None

        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return raw, None
        else:
            parsed = raw

        if isinstance(parsed, dict):
            output = parsed.get("output")
            if output is None and parsed:
                # dumping remaining structure if output missing
                output = json.dumps(parsed, ensure_ascii=False)
            metadata = parsed.get("metadata")
            return output, metadata if isinstance(metadata, dict) else None

        return str(parsed), None

    @staticmethod
    def _group_events_by_api_call_id(
        normalized_events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Merge assistant events from the same Codex model request into one step."""
        result: list[dict[str, Any]] = []
        groups: dict[str, dict[str, Any]] = {}
        group_order: list[str] = []

        def flush() -> None:
            for group_id in group_order:
                group = groups.pop(group_id, None)
                if group is None:
                    continue
                group["tool_calls"].sort(key=lambda tc: tc.get("tool_order", 0))
                message_parts = [
                    part
                    for part in group.pop("message_parts")
                    if isinstance(part, str) and part
                ]
                group["text"] = "\n\n".join(message_parts)
                result.append(group)
            group_order.clear()

        for event in normalized_events:
            api_call_id = event.get("api_call_id")
            kind = event.get("kind")
            role = event.get("role")

            if kind == "message" and role != "assistant":
                flush()
                result.append(event)
                continue

            if not isinstance(api_call_id, str):
                flush()
                result.append(event)
                continue

            if api_call_id not in groups:
                groups[api_call_id] = {
                    "kind": "bundled",
                    "api_call_id": api_call_id,
                    "codex_turn_id": event.get("codex_turn_id"),
                    "timestamp": event.get("timestamp"),
                    "message_parts": [],
                    "reasoning": None,
                    "tool_calls": [],
                    "metrics": event.get("metrics"),
                }
                group_order.append(api_call_id)

            group = groups[api_call_id]
            if kind == "message":
                text = event.get("text")
                if isinstance(text, str) and text:
                    group["message_parts"].append(text)
                if event.get("reasoning"):
                    group["reasoning"] = event["reasoning"]
                if event.get("timestamp"):
                    group["timestamp"] = event["timestamp"]
            elif kind == "tool_call":
                group["tool_calls"].append(event)
                if not group["reasoning"] and event.get("reasoning"):
                    group["reasoning"] = event["reasoning"]
                if not group.get("metrics") and event.get("metrics"):
                    group["metrics"] = event["metrics"]

        flush()
        return result

    @staticmethod
    def _metrics_from_token_count_payload(
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        info = payload.get("info")
        if not isinstance(info, dict):
            return None

        last_usage = info.get("last_token_usage")
        if not isinstance(last_usage, dict):
            return None

        prompt_tokens = last_usage.get("input_tokens")
        completion_tokens = last_usage.get("output_tokens")
        cached_tokens = last_usage.get("cached_input_tokens")
        reasoning_tokens = last_usage.get("reasoning_output_tokens")
        total_tokens = last_usage.get("total_tokens")

        return {
            "prompt_tokens": prompt_tokens if prompt_tokens else None,
            "completion_tokens": completion_tokens or None,
            "cached_tokens": cached_tokens or None,
            "extra": {
                "reasoning_output_tokens": reasoning_tokens,
                "total_tokens": total_tokens,
            },
        }

    def _convert_event_to_step(self, event: dict[str, Any], step_id: int) -> Step:
        """Convert a normalized Codex event dictionary into an ATIF step."""
        kind = event.get("kind")
        timestamp = event.get("timestamp")

        if kind == "message":
            role = event.get("role", "user")
            text = event.get("text", "")
            reasoning = event.get("reasoning")
            source: Literal["system", "user", "agent"]
            if role == "assistant":
                source = "agent"
            elif role == "user":
                source = "user"
            else:
                source = "system"

            extra = event.get("extra")

            return Step(
                step_id=step_id,
                timestamp=timestamp,
                source=source,
                message=text,
                reasoning_content=reasoning
                if source == "agent" and reasoning
                else None,
                model_name=self.model_name
                if source == "agent" and self.model_name
                else None,
                llm_call_count=1 if source == "agent" else None,
                extra=extra if extra else None,
            )

        if kind == "tool_call":
            call_id = event.get("call_id", "")
            tool_name = event.get("tool_name", "")
            reasoning = event.get("reasoning")
            arguments = event.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {"value": arguments}

            tool_call = ToolCall(
                tool_call_id=call_id,
                function_name=tool_name,
                arguments=arguments,
            )

            observation: Observation | None = None
            output_text = event.get("output")
            if output_text is not None:
                observation = Observation(
                    results=[
                        ObservationResult(
                            source_call_id=call_id or None,
                            content=output_text,
                        )
                    ]
                )

            metrics_payload = event.get("metrics")
            metrics: Metrics | None = None
            if isinstance(metrics_payload, dict):
                metrics = Metrics(**metrics_payload)

            extra: dict[str, Any] | None = None
            metadata = event.get("metadata")
            if metadata:
                extra = {"tool_metadata": metadata}
            raw_arguments = event.get("raw_arguments")
            if raw_arguments:
                extra = extra or {}
                extra["raw_arguments"] = raw_arguments
            status = event.get("status")
            if status:
                extra = extra or {}
                extra["status"] = status
            api_call_id = event.get("api_call_id")
            if api_call_id:
                extra = extra or {}
                extra["api_call_id"] = api_call_id
            codex_turn_id = event.get("codex_turn_id")
            if codex_turn_id:
                extra = extra or {}
                extra["codex_turn_id"] = codex_turn_id

            message = event.get("message") or ""

            return Step(
                step_id=step_id,
                timestamp=timestamp,
                source="agent",
                message=message,
                tool_calls=[tool_call],
                observation=observation,
                model_name=self.model_name if self.model_name else None,
                reasoning_content=reasoning if reasoning else None,
                metrics=metrics,
                llm_call_count=1,
                extra=extra,
            )

        if kind == "bundled":
            text = event.get("text", "")
            reasoning = event.get("reasoning")

            tool_calls: list[ToolCall] = []
            observation_results: list[ObservationResult] = []
            for tc in event.get("tool_calls", []):
                call_id = tc.get("call_id", "")
                arguments = tc.get("arguments") or {}
                if not isinstance(arguments, dict):
                    arguments = {"value": arguments}

                tool_calls.append(
                    ToolCall(
                        tool_call_id=call_id,
                        function_name=tc.get("tool_name", ""),
                        arguments=arguments,
                    )
                )
                observation_results.append(
                    ObservationResult(
                        source_call_id=call_id or None,
                        content=tc.get("output"),
                    )
                )

            extra: dict[str, Any] | None = None
            api_call_id = event.get("api_call_id")
            if api_call_id:
                extra = {"api_call_id": api_call_id}
            codex_turn_id = event.get("codex_turn_id")
            if codex_turn_id:
                extra = extra or {}
                extra["codex_turn_id"] = codex_turn_id

            tool_details: dict[str, Any] = {}
            for tc in event.get("tool_calls", []):
                call_id = tc.get("call_id", "")
                details: dict[str, Any] = {}
                for source_key, target_key in (
                    ("metadata", "metadata"),
                    ("raw_arguments", "raw_arguments"),
                    ("status", "status"),
                ):
                    value = tc.get(source_key)
                    if value:
                        details[target_key] = value
                if details:
                    tool_details[call_id] = details
            if tool_details:
                extra = extra or {}
                extra["tool_call_details"] = tool_details

            observation = (
                Observation(results=observation_results)
                if observation_results
                else None
            )

            return Step(
                step_id=step_id,
                timestamp=event.get("timestamp"),
                source="agent",
                message=text,
                model_name=self.model_name if self.model_name else None,
                reasoning_content=reasoning if reasoning else None,
                tool_calls=tool_calls or None,
                observation=observation,
                metrics=Metrics(**event["metrics"]) if event.get("metrics") else None,
                llm_call_count=1,
                extra=extra,
            )

        raise ValueError(f"Unsupported event kind '{kind}'")

    def _compute_cost_from_pricing(
        self,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        cached_tokens: int | None,
    ) -> float | None:
        """Compute total cost in USD from token counts via LiteLLM's pricing table.

        Codex CLI's session JSONL never includes a cost field, so we apply
        LiteLLM's per-token rates to the aggregated counts ourselves. Returns
        None when the model is missing from the table — caller should leave
        cost_usd unset rather than report a misleading $0.
        """
        if not self.model_name:
            return None

        try:
            import litellm
        except ImportError:
            self.logger.debug("litellm not available; leaving codex cost_usd as None")
            return None

        pricing: dict[str, Any] | None = None
        for key in (self.model_name, self.model_name.split("/", 1)[-1]):
            entry = litellm.model_cost.get(key)
            if entry:
                pricing = entry
                break

        if pricing is None:
            self.logger.debug(
                "No LiteLLM pricing entry for model '%s'; leaving codex "
                "cost_usd as None",
                self.model_name,
            )
            return None

        input_rate = pricing.get("input_cost_per_token") or 0.0
        output_rate = pricing.get("output_cost_per_token") or 0.0
        cache_read_rate = pricing.get("cache_read_input_token_cost", input_rate)
        if cache_read_rate is None:
            cache_read_rate = input_rate

        uncached_input = max(0, (prompt_tokens or 0) - (cached_tokens or 0))
        cached = cached_tokens or 0
        output = completion_tokens or 0

        return (
            uncached_input * input_rate
            + cached * cache_read_rate
            + output * output_rate
        )

    def _convert_events_to_trajectory(self, session_dir: Path) -> Trajectory | None:
        """Convert Codex session JSONL events into an ATIF trajectory."""
        session_files = list(session_dir.glob("*.jsonl"))

        if not session_files:
            self.logger.debug(f"No Codex session files found in {session_dir}")
            return None

        session_file = session_files[0]

        raw_events: list[dict[str, Any]] = []
        with open(session_file, "r") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    raw_events.append(json.loads(stripped))
                except json.JSONDecodeError as exc:
                    self.logger.debug(
                        f"Skipping malformed JSONL line in {session_file}: {exc}"
                    )

        if not raw_events:
            return None

        session_meta = next(
            (e for e in raw_events if e.get("type") == "session_meta"), None
        )
        session_id = (
            session_meta.get("payload", {}).get("id")
            if session_meta and isinstance(session_meta, dict)
            else session_dir.name
        )

        agent_version = "unknown"
        agent_extra: dict[str, Any] | None = None
        default_model_name: str | None = None

        if session_meta:
            payload = session_meta.get("payload", {})
            agent_version = payload.get("cli_version") or agent_version
            extra: dict[str, Any] = {}
            for key in ("originator", "cwd", "git", "instructions"):
                value = payload.get(key)
                if value is not None:
                    extra[key] = value
            agent_extra = extra or None

        if agent_version == "unknown" and self._version:
            agent_version = self._version

        for event in raw_events:
            if event.get("type") == "turn_context":
                model_name = event.get("payload", {}).get("model")
                if isinstance(model_name, str):
                    default_model_name = model_name
                    break

        if default_model_name is None:
            default_model_name = self.model_name

        # normalize events to a structure suitable for conversion into Steps
        normalized_events: list[dict[str, Any]] = []
        pending_calls: dict[str, dict[str, Any]] = {}
        pending_reasoning: str | None = None
        codex_turn_id: str | None = None
        api_call_index = 1
        current_api_call_id = f"api_call_{api_call_index}"
        api_call_metrics: dict[str, dict[str, Any]] = {}
        saw_model_output_in_api_call = False
        tool_order_counter = 0

        def record_model_output() -> None:
            nonlocal saw_model_output_in_api_call
            saw_model_output_in_api_call = True

        def finish_api_call(token_count_payload: dict[str, Any]) -> None:
            nonlocal api_call_index, current_api_call_id, saw_model_output_in_api_call
            nonlocal tool_order_counter

            if not saw_model_output_in_api_call:
                return

            metrics = self._metrics_from_token_count_payload(token_count_payload)
            if metrics:
                api_call_metrics[current_api_call_id] = metrics

            api_call_index += 1
            current_api_call_id = f"api_call_{api_call_index}"
            saw_model_output_in_api_call = False
            tool_order_counter = 0

        for event in raw_events:
            etype = event.get("type")
            payload = event.get("payload", {})
            timestamp = event.get("timestamp")

            if etype == "event_msg" and isinstance(payload, dict):
                event_type = payload.get("type")
                if event_type in {"task_started", "turn_started"}:
                    turn_id = payload.get("turn_id")
                    codex_turn_id = turn_id if isinstance(turn_id, str) else None
                elif event_type in {"task_complete", "turn_complete", "turn_aborted"}:
                    codex_turn_id = None
                elif event_type == "token_count":
                    # A token_count event closes one model API call.
                    finish_api_call(payload)
                continue

            if etype == "turn_context":
                turn_id = payload.get("turn_id") if isinstance(payload, dict) else None
                if isinstance(turn_id, str) and codex_turn_id is None:
                    codex_turn_id = turn_id
                continue

            if etype != "response_item":
                continue

            payload_type = payload.get("type")
            if payload_type == "reasoning":
                summary = payload.get("summary")
                if isinstance(summary, list) and summary:
                    reasoning_parts: list[str] = []
                    for item in summary:
                        if isinstance(item, str):
                            reasoning_parts.append(item)
                        elif isinstance(item, dict):
                            text = item.get("text")
                            if isinstance(text, str):
                                reasoning_parts.append(text)
                    pending_reasoning = (
                        "\n".join(reasoning_parts) if reasoning_parts else None
                    )
                else:
                    pending_reasoning = None
                continue

            if payload_type == "message":
                content = payload.get("content", [])
                text = (
                    self._extract_message_text(content)
                    if isinstance(content, list)
                    else ""
                )
                normalized_events.append(
                    {
                        "kind": "message",
                        "api_call_id": current_api_call_id,
                        "codex_turn_id": codex_turn_id,
                        "timestamp": timestamp,
                        "role": payload.get("role", "user"),
                        "text": text,
                        "reasoning": pending_reasoning
                        if payload.get("role") == "assistant"
                        else None,
                    }
                )
                if payload.get("role") == "assistant":
                    record_model_output()
                pending_reasoning = None
                continue

            if payload_type == "web_search_call":
                action = payload.get("action") or {}
                action_type = action.get("type", "")
                arguments: dict[str, Any] = {"action_type": action_type}
                if "query" in action:
                    arguments["query"] = action["query"]
                if "queries" in action:
                    arguments["queries"] = action["queries"]
                if "url" in action:
                    arguments["url"] = action["url"]

                normalized_events.append(
                    {
                        "kind": "tool_call",
                        "api_call_id": current_api_call_id,
                        "codex_turn_id": codex_turn_id,
                        "tool_order": tool_order_counter,
                        "timestamp": timestamp,
                        "call_id": "",
                        "tool_name": "web_search_call",
                        "arguments": arguments,
                        "raw_arguments": None,
                        "reasoning": pending_reasoning,
                        "status": payload.get("status"),
                        "message": None,
                    }
                )
                tool_order_counter += 1
                record_model_output()
                pending_reasoning = None
                continue

            if payload_type in {"function_call", "custom_tool_call"}:
                call_id = payload.get("call_id")
                if not call_id:
                    continue

                raw_args_key = (
                    "arguments" if payload_type == "function_call" else "input"
                )
                raw_arguments = payload.get(raw_args_key)
                try:
                    parsed_args = json.loads(raw_arguments)
                except (json.JSONDecodeError, TypeError):
                    if isinstance(raw_arguments, str):
                        parsed_args = {"input": raw_arguments}
                    elif raw_arguments is None:
                        parsed_args = {}
                    else:
                        parsed_args = {"value": raw_arguments}

                pending_calls[call_id] = {
                    "kind": "tool_call",
                    "api_call_id": current_api_call_id,
                    "codex_turn_id": codex_turn_id,
                    "tool_order": tool_order_counter,
                    "timestamp": timestamp,
                    "call_id": call_id,
                    "tool_name": payload.get("name") or "",
                    "arguments": parsed_args,
                    "raw_arguments": raw_arguments,
                    "reasoning": pending_reasoning,
                    "status": payload.get("status"),
                    "message": None,
                }
                tool_order_counter += 1
                record_model_output()
                pending_reasoning = None
                continue

            if payload_type in {"function_call_output", "custom_tool_call_output"}:
                call_id = payload.get("call_id")
                output_text, metadata = self._parse_output_blob(payload.get("output"))

                call_info = pending_calls.pop(call_id, None) if call_id else None

                if call_info is None:
                    call_info = {
                        "kind": "tool_call",
                        "api_call_id": current_api_call_id,
                        "codex_turn_id": codex_turn_id,
                        "tool_order": tool_order_counter,
                        "timestamp": timestamp,
                        "call_id": call_id or "",
                        "tool_name": payload.get("name", "") or "",
                        "arguments": {},
                        "raw_arguments": None,
                        "reasoning": pending_reasoning,
                        "status": None,
                        "message": None,
                    }
                    tool_order_counter += 1

                call_info["output"] = output_text
                call_info["metadata"] = metadata
                call_info["timestamp"] = call_info.get("timestamp") or timestamp
                normalized_events.append(call_info)
                pending_reasoning = None
                continue

        for norm_event in normalized_events:
            event_api_call_id = norm_event.get("api_call_id")
            if (
                isinstance(event_api_call_id, str)
                and event_api_call_id in api_call_metrics
            ):
                norm_event["metrics"] = api_call_metrics[event_api_call_id]

        grouped_events = self._group_events_by_api_call_id(normalized_events)

        steps: list[Step] = []
        for idx, norm_event in enumerate(grouped_events, start=1):
            try:
                step = self._convert_event_to_step(norm_event, idx)
            except ValueError as exc:
                self.logger.debug(f"Skipping event during step conversion: {exc}")
                continue

            # Provide default model name if not set for agent steps
            if step.source == "agent" and not step.model_name and default_model_name:
                step.model_name = default_model_name

            steps.append(step)

        if not steps:
            self.logger.debug("No valid steps produced from Codex session")
            return None

        # Extract final metrics from the last token_count event with totals
        total_metrics: FinalMetrics | None = None
        for event in reversed(raw_events):
            if event.get("type") != "event_msg":
                continue
            payload = event.get("payload", {})
            if not isinstance(payload, dict) or payload.get("type") != "token_count":
                continue

            info = payload.get("info")
            if not isinstance(info, dict):
                continue

            total_usage = info.get("total_token_usage")
            if not isinstance(total_usage, dict):
                continue

            prompt_tokens = total_usage.get("input_tokens")
            completion_tokens = total_usage.get("output_tokens")
            reasoning_tokens = total_usage.get("reasoning_output_tokens")
            cached_tokens = total_usage.get("cached_input_tokens")
            overall_tokens = total_usage.get("total_tokens")

            # Codex CLI does not include cost in token_count events, so fall
            # back to LiteLLM's pricing table when the field is absent. Use
            # explicit `is None` checks so a legitimate $0 cost reported by
            # the CLI isn't mistaken for a missing field.
            total_cost_usd = info.get("total_cost")
            if total_cost_usd is None:
                total_cost_usd = info.get("cost_usd")
            if total_cost_usd is None:
                total_cost_usd = self._compute_cost_from_pricing(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cached_tokens=cached_tokens,
                )

            final_extra: dict[str, Any] | None = {
                "reasoning_output_tokens": reasoning_tokens,
                "total_tokens": overall_tokens,
                "last_token_usage": info.get("last_token_usage"),
            }

            total_metrics = FinalMetrics(
                total_prompt_tokens=prompt_tokens if prompt_tokens else None,
                total_completion_tokens=completion_tokens or None,
                total_cached_tokens=cached_tokens or None,
                total_cost_usd=total_cost_usd,
                total_steps=len(steps),
                extra=final_extra,
            )
            break

        trajectory = Trajectory(
            schema_version="ATIF-v1.7",
            session_id=session_id,
            agent=Agent(
                name="codex",
                version=agent_version,
                model_name=default_model_name,
                extra=agent_extra,
            ),
            steps=steps,
            final_metrics=total_metrics,
        )

        return trajectory

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        """
        Populate the agent context after Codex finishes executing.

        Converts the Codex session JSONL file into an ATIF trajectory, persists it,
        and propagates usage metrics back to the Harbor context.
        """
        session_dir = self._get_session_dir()
        if not session_dir:
            self.logger.debug("No Codex session directory found")
            return

        try:
            trajectory = self._convert_events_to_trajectory(session_dir)
        except Exception:
            self.logger.exception("Failed to convert Codex events to trajectory")
            return

        if not trajectory:
            self.logger.debug("Failed to convert Codex session to trajectory")
            return

        trajectory_path = self.logs_dir / "trajectory.json"
        try:
            with open(trajectory_path, "w") as handle:
                handle.write(format_trajectory_json(trajectory.to_json_dict()))
            self.logger.debug(f"Wrote Codex trajectory to {trajectory_path}")
        except OSError as exc:
            self.logger.debug(
                f"Failed to write trajectory file {trajectory_path}: {exc}"
            )

        if trajectory.final_metrics:
            metrics = trajectory.final_metrics
            context.cost_usd = metrics.total_cost_usd
            context.n_input_tokens = metrics.total_prompt_tokens or 0
            context.n_cache_tokens = metrics.total_cached_tokens or 0
            context.n_output_tokens = metrics.total_completion_tokens or 0

    def _build_register_skills_command(self) -> str | None:
        """Return a shell command that copies skills to Codex's skills directory."""
        if not self.skills_dir:
            return None
        return (
            f"mkdir -p $HOME/.agents/skills && "
            f"cp -r {shlex.quote(self.skills_dir)}/* "
            f"$HOME/.agents/skills/ 2>/dev/null || true"
        )

    def _build_register_mcp_servers_command(self) -> str | None:
        """Return a shell command that writes MCP config to $CODEX_HOME/config.toml."""
        if not self.mcp_servers:
            return None
        lines: list[str] = []
        for server in self.mcp_servers:
            lines.append(f"[mcp_servers.{server.name}]")
            if server.transport == "stdio":
                cmd_parts = [server.command] + server.args if server.command else []
                lines.append(f'command = "{shlex.join(cmd_parts)}"')
            else:
                lines.append(f'url = "{server.url}"')
            lines.append("")
        escaped_config = shlex.quote("\n".join(lines))
        return f'echo {escaped_config} >> "$CODEX_HOME/config.toml"'

    def _resolve_auth_json_path(self) -> Path | None:
        """Resolve which auth.json to inject, if any.

        Defaults to None (OPENAI_API_KEY auth). Opt into auth.json auth via:
          - CODEX_AUTH_JSON_PATH=<path> → use that specific file
          - CODEX_FORCE_AUTH_JSON=<truthy> → use ~/.codex/auth.json
        """
        explicit = self._get_env("CODEX_AUTH_JSON_PATH")
        if explicit:
            p = Path(explicit)
            if not p.is_file():
                raise ValueError(
                    f"CODEX_AUTH_JSON_PATH points to non-existent file: {explicit}"
                )
            return p

        if parse_bool_env_value(
            self._get_env("CODEX_FORCE_AUTH_JSON"),
            name="CODEX_FORCE_AUTH_JSON",
            default=False,
        ):
            default = Path.home() / ".codex" / "auth.json"
            if not default.is_file():
                raise ValueError(
                    f"CODEX_FORCE_AUTH_JSON is set but {default} does not exist"
                )
            return default

        return None

    def _build_exec_command(self, instruction: str, *, resume: bool) -> str:
        """Build the ``codex exec`` command line (fresh exec, or
        ``resume --last`` when ``resume=True``).

        The ``--json`` event stream is teed to ``/logs/agent/codex.txt``;
        the usersim subclass reads each turn's reply back from that file.
        """
        escaped_instruction = shlex.quote(instruction)

        if not self.model_name:
            raise ValueError("Model name is required")

        model = self.model_name.removeprefix("openai/")

        # Build command with optional CLI config flags from descriptors.
        cli_flags = self.build_cli_flags()
        cli_flags_arg = (cli_flags + " ") if cli_flags else ""

        output_path = (EnvironmentPaths.agent_dir / self._OUTPUT_FILENAME).as_posix()
        # web_search knob — configurable, with a safe default.
        #   HARBOR_CODEX_WEB_SEARCH = disabled | live | auto
        #   unset -> leave codex/mode default untouched. (The canonical run
        #            configs set it to "disabled" explicitly for anti-cheat.)
        # WHY a CLI `-c` and not config.toml / the generic passthrough:
        #   - --dangerously-bypass-approvals-and-sandbox (YOLO) flips the
        #     top-level `web_search` to "live" by default and offers the tool.
        #     GLM-5.2 then calls it with malformed-JSON args → endpoint
        #     /v1/responses 400 → turn.failed → trial discarded (see §7.1).
        #   - CLI `-c` is what we verified (v3) reliably overrides the YOLO
        #     default (CLI beats config.toml and mode defaults); a plain
        #     config.toml value overriding YOLO is unverified.
        #   - Keeping it a `-c` (not written into config.toml) also avoids a
        #     duplicate-key TOML error if someone ALSO sets web_search via
        #     HARBOR_CODEX_EXTRA_CONFIG_TOML.
        web_search_value = (self._get_env("HARBOR_CODEX_WEB_SEARCH") or "").strip()
        web_search_flag = (
            f"-c 'web_search=\"{web_search_value}\"' " if web_search_value else ""
        )
        return (
            "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; "
            f"codex exec {'resume --last ' if resume else ''}"
            "--dangerously-bypass-approvals-and-sandbox "
            "--skip-git-repo-check "
            f"--model {model} "
            "--json "
            "--enable unified_exec "
            f"{web_search_flag}"
            f"{cli_flags_arg}"
            "-- "  # end of flags
            f"{escaped_instruction} "
            f"2>&1 </dev/null | tee {shlex.quote(output_path)}"
        )

    async def _exec_codex_setup(
        self, environment: BaseEnvironment, *, resume: bool
    ) -> dict[str, str]:
        """Run the container-side Codex setup; return the env for later execs.

        Creates CODEX_HOME / secrets / agent-log dirs, wires auth (auth.json
        upload or OPENAI_API_KEY heredoc), writes config.toml (base_url /
        provider table / extra TOML), registers skills and MCP servers, and —
        when ``resume=True`` — restores sessions from the persisted agent
        logs so ``codex exec resume --last`` can find them.
        """
        # Auth resolution:
        #   1. CODEX_AUTH_JSON_PATH=<path> → use that specific auth.json file
        #   2. CODEX_FORCE_AUTH_JSON=<truthy> → use ~/.codex/auth.json
        #   3. Default: use OPENAI_API_KEY
        auth_json_path = self._resolve_auth_json_path()

        remote_codex_home = self._REMOTE_CODEX_HOME.as_posix()
        remote_secrets_dir = self._REMOTE_CODEX_SECRETS_DIR.as_posix()
        remote_auth_path = (self._REMOTE_CODEX_SECRETS_DIR / "auth.json").as_posix()
        agent_sessions_dir = (EnvironmentPaths.agent_dir / "sessions").as_posix()

        env: dict[str, str] = {
            "CODEX_HOME": remote_codex_home,
        }

        await self.exec_as_agent(
            environment,
            command=(
                f'mkdir -p "$CODEX_HOME" {shlex.quote(remote_secrets_dir)} '
                f"{shlex.quote(EnvironmentPaths.agent_dir.as_posix())}"
            ),
            env=env,
        )

        if auth_json_path:
            self.logger.debug("Codex auth: using auth.json from %s", auth_json_path)
            await environment.upload_file(auth_json_path, remote_auth_path)
            # upload_file copies as root; fix ownership so the agent user can read it
            if environment.default_user is not None:
                await self.exec_as_root(
                    environment,
                    command=f"chown {environment.default_user} {remote_auth_path}",
                )
            setup_command = (
                f'ln -sf {shlex.quote(remote_auth_path)} "$CODEX_HOME/auth.json"\n'
            )
        else:
            self.logger.debug("Codex auth: using OPENAI_API_KEY")
            env["OPENAI_API_KEY"] = (
                self._openai_api_key
                if self._openai_api_key is not None
                else self._get_env("OPENAI_API_KEY") or ""
            )
            setup_command = (
                f"cat >{shlex.quote(remote_auth_path)} <<EOF\n"
                '{\n  "OPENAI_API_KEY": "${OPENAI_API_KEY}"\n}\nEOF\n'
                f"ln -sf {shlex.quote(remote_auth_path)} "
                '"$CODEX_HOME/auth.json"\n'
            )

        openai_base_url = (
            self._openai_base_url
            if self._openai_base_url is not None
            else self._get_env("OPENAI_BASE_URL")
        )
        if openai_base_url:
            env["OPENAI_BASE_URL"] = openai_base_url

        # codex 0.118.0 only honors openai_base_url from config.toml, not the env var.
        config_toml_block = ""
        # The [model_providers.*] TABLE must be appended LAST: TOML requires every
        # top-level key (HARBOR_CODEX_EXTRA_CONFIG_TOML content) to precede any
        # [table] header, else they get absorbed into the provider table and
        # codex silently ignores them as unknown provider fields.
        provider_table_block = ""
        if openai_base_url:
            # codex 0.142 defaults to a WebSocket Responses transport
            # (wss://.../responses). Reverse-proxy gateways that do not allow WS
            # upgrades answer 403, and codex burns 5 reconnect attempts before
            # falling back to HTTPS — wasteful latency and a source of turn
            # failures under load. When HARBOR_CODEX_DISABLE_WEBSOCKET=1 is set,
            # route through a custom provider with supports_websockets=false so
            # codex uses HTTPS/SSE directly. The provider keeps env-key auth
            # (OPENAI_API_KEY), so a key-injecting router still receives whatever
            # the agent sends.
            if self._get_env("HARBOR_CODEX_DISABLE_WEBSOCKET") == "1":
                # An SSE stream from a busy router can stall mid-response; codex
                # then reconnects up to stream_max_retries times, waiting
                # stream_idle_timeout_ms for the next chunk each attempt. The
                # codex defaults (5 retries / 300000ms) were exhausted under load
                # in GLM-5.2 runs ("idle timeout waiting for SSE"), failing turns.
                # Bump the defaults and let them be tuned per-run via env.
                def _int_env(key: str, default: int) -> int:
                    raw = self._get_env(key)
                    if raw is None or str(raw).strip() == "":
                        return default
                    try:
                        return int(str(raw).strip())
                    except ValueError:
                        return default

                stream_max_retries = _int_env("HARBOR_CODEX_STREAM_MAX_RETRIES", 10)
                stream_idle_timeout_ms = _int_env(
                    "HARBOR_CODEX_STREAM_IDLE_TIMEOUT_MS", 600000
                )
                request_max_retries = _int_env("HARBOR_CODEX_REQUEST_MAX_RETRIES", 5)
                # Top-level keys only here; the [model_providers.openai_nows]
                # TABLE goes into provider_table_block, appended LAST (see above).
                config_toml_block = (
                    '\ncat >>"$CODEX_HOME/config.toml" <<TOML\n'
                    'openai_base_url = "${OPENAI_BASE_URL}"\n'
                    'model_provider = "openai_nows"\n'
                    "TOML"
                )
                provider_table_block = (
                    '\ncat >>"$CODEX_HOME/config.toml" <<TOML\n'
                    "[model_providers.openai_nows]\n"
                    'name = "openai-nows"\n'
                    'base_url = "${OPENAI_BASE_URL}"\n'
                    # wire_api 可经 HARBOR_CODEX_WIRE_API 覆盖（默认 responses
                    # 不变）：Bedrock 后端拒 assistant prefill（"conversation
                    # must end with a user message"），其 /v1/responses 转换
                    # 会 400，而 /v1/chat/completions 接受——claude 系模型
                    # 经 Bedrock 走 codex 时必须切 "chat"（2026-09-16 冒烟实测）。
                    f'wire_api = "{self._get_env("HARBOR_CODEX_WIRE_API") or "responses"}"\n'
                    "requires_openai_auth = false\n"
                    "supports_websockets = false\n"
                    f"stream_max_retries = {stream_max_retries}\n"
                    f"stream_idle_timeout_ms = {stream_idle_timeout_ms}\n"
                    f"request_max_retries = {request_max_retries}\n"
                    "TOML"
                )
            else:
                config_toml_block = (
                    '\ncat >>"$CODEX_HOME/config.toml" <<TOML\n'
                    'openai_base_url = "${OPENAI_BASE_URL}"\n'
                    "TOML"
                )

        setup_command += config_toml_block

        skills_command = self._build_register_skills_command()
        if skills_command:
            setup_command += f"\n{skills_command}"

        mcp_command = self._build_register_mcp_servers_command()
        if mcp_command:
            setup_command += f"\n{mcp_command}"

        # Generic config passthrough: append arbitrary TOML from
        # HARBOR_CODEX_EXTRA_CONFIG_TOML to $CODEX_HOME/config.toml. Lets any
        # TOP-LEVEL codex config key (model_context_window,
        # model_auto_compact_token_limit[_scope], tool_output_token_limit,
        # model_reasoning_effort/summary, ...) be tuned from the run config
        # WITHOUT editing this file. Written BEFORE the provider [table] below
        # (must stay top-level). Quoted heredoc → content is written verbatim
        # (no shell $-expansion of the TOML).
        extra_config_toml = self._get_env("HARBOR_CODEX_EXTRA_CONFIG_TOML")
        if extra_config_toml and extra_config_toml.strip():
            setup_command += (
                '\ncat >>"$CODEX_HOME/config.toml" '
                "<<'HARBOR_EXTRA_TOML'\n"
                f"{extra_config_toml.rstrip()}\n"
                "HARBOR_EXTRA_TOML"
            )

        # ★ Append the [model_providers.openai_nows] TABLE LAST — after every
        # top-level key above (base_url / model_provider / EXTRA_CONFIG_TOML).
        # Otherwise those keys land inside the table and are silently ignored
        # by codex (confirmed bug 2026-07-22).
        setup_command += provider_table_block

        if resume:
            setup_command += (
                f"\nif [ ! -d {shlex.quote(agent_sessions_dir)} ]; then\n"
                '  echo "Cannot resume Codex: no previous session logs found" >&2\n'
                "  exit 1\n"
                "fi\n"
                'rm -rf "$CODEX_HOME/sessions"\n'
                f"cp -R {shlex.quote(agent_sessions_dir)} "
                '"$CODEX_HOME/sessions"'
            )

        if setup_command.strip():
            await self.exec_as_agent(
                environment,
                command=setup_command,
                env=env,
            )
        return env

    async def _persist_sessions(
        self, environment: BaseEnvironment, env: dict[str, str]
    ) -> None:
        """Best-effort: copy ``$CODEX_HOME/sessions`` back into the agent logs
        dir (survives CODEX_HOME cleanup; resume source and trajectory input)."""
        sessions_path = (EnvironmentPaths.agent_dir / "sessions").as_posix()
        try:
            await self.exec_as_agent(
                environment,
                command=(
                    f"mkdir -p {EnvironmentPaths.agent_dir.as_posix()}\n"
                    'if [ -d "$CODEX_HOME/sessions" ]; then\n'
                    f"  rm -rf {shlex.quote(sessions_path)}\n"
                    f'  cp -R "$CODEX_HOME/sessions" {shlex.quote(sessions_path)}\n'
                    "fi"
                ),
                env=env,
            )
        except Exception:
            pass

    async def _cleanup_codex_home(
        self, environment: BaseEnvironment, env: dict[str, str]
    ) -> None:
        """Best-effort: remove the secrets dir and CODEX_HOME from the container."""
        try:
            await self.exec_as_agent(
                environment,
                command=(
                    f"rm -rf {shlex.quote(self._REMOTE_CODEX_SECRETS_DIR.as_posix())} "
                    '"$CODEX_HOME"'
                ),
                env=env,
            )
        except Exception:
            pass

    @override
    @with_prompt_template
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        if not self.model_name:
            raise ValueError("Model name is required")

        env = await self._exec_codex_setup(environment, resume=self._resume)
        try:
            await self.exec_as_agent(
                environment,
                command=self._build_exec_command(instruction, resume=self._resume),
                env=env,
            )
        finally:
            await self._persist_sessions(environment, env)
            # cleanup - best effort
            await self._cleanup_codex_home(environment, env)
