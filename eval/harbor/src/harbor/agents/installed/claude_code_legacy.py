import base64
import datetime
import json
import os
import shlex
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

from harbor.agents.installed.base import (
    BaseInstalledAgent,
    CliFlag,
    EnvVar,
    with_prompt_template,
)
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.agent.name import AgentName
from harbor.models.task.config import UserModelConfig
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

if TYPE_CHECKING:
    from harbor.llms.lite_llm import LiteLLM

USER_MODEL_SYSTEM_PROMPT = """You are a user interacting with a coding agent. You are guiding the agent through a multi-turn task.

Based on the original task instruction and the agent's previous outputs, generate the next instruction for the agent. Your instruction should:
1. Build upon what the agent has already done
2. Guide the agent toward completing the overall task
3. Be clear and specific about what to do next
4. Reference the agent's previous output when relevant

Respond with ONLY the next instruction text, without any preamble or explanation."""


class ClaudeCode(BaseInstalledAgent):
    SUPPORTS_ATIF: bool = True
    SUPPORTS_RESUME: bool = True
    ALLOWED_TOOLS = [
        "Bash",
        "Edit",
        "Write",
        "Read",
        "Glob",
        "Grep",
        "LS",
        "WebFetch",
        "NotebookEdit",
        "NotebookRead",
        "TodoRead",
        "TodoWrite",
        "Agent",
        "Skill",
        "SlashCommand",
        "Task",
        "WebSearch",
    ]
    memory_dir: str | None
    _INSTALL_CHECK_COMMAND = (
        'export PATH="$HOME/.local/bin:$PATH"; command -v claude >/dev/null 2>&1'
    )
    _INSTALL_VERSION_COMMAND = 'export PATH="$HOME/.local/bin:$PATH"; claude --version'

    CLI_FLAGS = [
        CliFlag(
            "max_turns",
            cli="--max-turns",
            type="int",
            env_fallback="CLAUDE_CODE_MAX_TURNS",
        ),
        CliFlag(
            "reasoning_effort",
            cli="--effort",
            type="enum",
            choices=["low", "medium", "high", "xhigh", "max", "ultracode"],
            env_fallback="CLAUDE_CODE_EFFORT_LEVEL",
        ),
        CliFlag(
            "thinking",
            cli="--thinking",
            type="enum",
            choices=["enabled", "adaptive", "disabled"],
        ),
        CliFlag(
            "thinking_display",
            cli="--thinking-display",
            type="enum",
            choices=["summarized", "omitted"],
        ),
        CliFlag(
            "max_thinking_tokens",
            cli="--max-thinking-tokens",
            type="int",
            env_fallback="MAX_THINKING_TOKENS",
        ),
        CliFlag(
            "max_budget_usd",
            cli="--max-budget-usd",
            type="str",
        ),
        CliFlag(
            "fallback_model",
            cli="--fallback-model",
            type="str",
        ),
        CliFlag(
            "append_system_prompt",
            cli="--append-system-prompt",
            type="str",
        ),
        CliFlag(
            "allowed_tools",
            cli="--allowedTools",
            type="str",
        ),
        CliFlag(
            "disallowed_tools",
            cli="--disallowedTools",
            type="str",
        ),
        CliFlag(
            "permission_mode",
            cli="--permission-mode",
            type="enum",
            choices=[
                "default",
                "acceptEdits",
                "plan",
                "auto",
                "dontAsk",
                "bypassPermissions",
            ],
            default="bypassPermissions",
            format="--permission-mode={value}",
        ),
    ]
    ENV_VARS = [
        EnvVar(
            "max_thinking_tokens",
            env="MAX_THINKING_TOKENS",
            type="int",
            env_fallback="MAX_THINKING_TOKENS",
        ),
    ]

    @staticmethod
    @override
    def name() -> str:
        return AgentName.CLAUDE_CODE.value

    def __init__(
        self,
        logs_dir: Path,
        memory_dir: str | None = None,
        *args,
        **kwargs,
    ):
        self.memory_dir = memory_dir
        super().__init__(logs_dir, *args, **kwargs)

    @override
    def get_version_command(self) -> str | None:
        return 'export PATH="$HOME/.local/bin:$PATH"; claude --version'

    @staticmethod
    def _normalize_session_id(session_id: str) -> str:
        """Claude Code requires UUID session ids.

        For Harbor-managed interactive loops we may receive logical session ids
        like ``trial-name-user-sim``. Convert those deterministically to UUIDv5
        so repeated resumes keep targeting the same Claude session.
        """
        try:
            return str(uuid.UUID(session_id))
        except (ValueError, AttributeError, TypeError):
            return str(uuid.uuid5(uuid.NAMESPACE_URL, session_id))

    @override
    def parse_version(self, stdout: str) -> str:
        # Output formats seen: "1.0.18 (Claude Code)" or "claude v1.2.3"
        import re

        text = stdout.strip()
        match = re.search(r"(\d+\.\d+\.\d+)", text)
        if match:
            return match.group(1)
        return text

    def _build_install_command(self) -> str:
        default_version = shlex.quote(self._version) if self._version else '""'
        command = """
set -euo pipefail
trap 'echo "FAILED at line $LINENO"' ERR

export TERM="${TERM:-xterm}"
export PATH="$HOME/.local/bin:$PATH"
DEFAULT_CLAUDE_VERSION=__DEFAULT_VERSION__
CLAUDE_OFFLINE_DIR="${CLAUDE_CODE_OFFLINE_DIR:-/mnt/local/envs}"
CLAUDE_REQUIRE_OFFLINE="${CLAUDE_CODE_REQUIRE_OFFLINE:-${HARBOR_REQUIRE_OFFLINE_AGENT:-0}}"

detect_platform() {
  local os arch
  case "$(uname -s)" in
    Darwin) os="darwin" ;;
    Linux) os="linux" ;;
    *)
      echo "Unsupported OS: $(uname -s)" >&2
      exit 1
      ;;
  esac

  case "$(uname -m)" in
    x86_64|amd64) arch="x64" ;;
    arm64|aarch64) arch="arm64" ;;
    *)
      echo "Unsupported arch: $(uname -m)" >&2
      exit 1
      ;;
  esac

  if [ "$os" = "linux" ]; then
    if [ -f /lib/libc.musl-x86_64.so.1 ] || [ -f /lib/libc.musl-aarch64.so.1 ] || ldd /bin/ls 2>&1 | grep -q musl; then
      printf '%s\n' "linux-${arch}-musl"
    else
      printf '%s\n' "linux-${arch}"
    fi
  else
    printf '%s\n' "${os}-${arch}"
  fi
}

resolve_requested_target() {
  local requested="${CLAUDE_VERSION:-${DEFAULT_CLAUDE_VERSION}}"

  if [ -z "$requested" ]; then
    return 0
  fi

  if [[ "$requested" =~ ^(stable|latest|[0-9]+\\.[0-9]+\\.[0-9]+(-[^[:space:]]+)?)$ ]]; then
    printf '%s\n' "$requested"
    return 0
  fi

  echo "Invalid Claude version target: $requested" >&2
  exit 1
}

resolve_offline_binary() {
  local dir="$1"
  local platform="$2"
  local requested="$3"
  local candidate=""
  local version_from_file=""

  if [ ! -d "$dir" ]; then
    return 1
  fi

  if [ -n "$requested" ] && [[ "$requested" != "latest" && "$requested" != "stable" ]]; then
    candidate="$dir/claude-$requested-$platform"
    if [ -f "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  fi

  if [ -f "$dir/claude-version.txt" ]; then
    version_from_file="$(tr -d '[:space:]' < "$dir/claude-version.txt")"
    candidate="$dir/claude-$version_from_file-$platform"
    if [ -n "$version_from_file" ] && [ -f "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  fi

  candidate="$(find "$dir" -maxdepth 1 -type f -name "claude-*-$platform" | sort -V | tail -n 1 || true)"
  if [ -n "$candidate" ]; then
    printf '%s\n' "$candidate"
    return 0
  fi

  return 1
}

install_offline_binary() {
  local binary_path="$1"
  mkdir -p "$HOME/.local/bin"
  install -m 0755 "$binary_path" "$HOME/.local/bin/claude"
  echo "[claude-install] Installed Claude Code from offline binary: $(basename "$binary_path")"
}

validate_installed_claude() {
  local version_log="${1:-/tmp/claude-version.log}"
  if "$HOME/.local/bin/claude" --version >"$version_log" 2>&1; then
    cat "$version_log"
    return 0
  fi
  cat "$version_log" || true
  return 1
}

download_file() {
  local url="$1" output="$2"
  if command -v curl >/dev/null 2>&1; then
    if [ -n "$output" ]; then
      curl -fsSL -o "$output" "$url"
    else
      curl -fsSL "$url"
    fi
  elif command -v wget >/dev/null 2>&1; then
    if [ -n "$output" ]; then
      wget -q -O "$output" "$url"
    else
      wget -q -O - "$url"
    fi
  else
    echo "Either curl or wget is required" >&2
    exit 1
  fi
}

get_checksum_from_manifest() {
  local json="$1" platform="$2"
  json=$(echo "$json" | tr -d '\n\r\t' | sed 's/ \\+/ /g')
  if [[ $json =~ \"$platform\"[^}]*\"checksum\"[[:space:]]*:[[:space:]]*\"([a-f0-9]{64})\" ]]; then
    echo "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

install_online_binary() {
  local target="$1"
  local bucket="https://storage.googleapis.com/claude-code-dist-86c565f3-f756-42ad-8dfa-d59b1c096819/claude-code-releases"
  local download_dir="$HOME/.claude/downloads"
  local platform version manifest_json checksum binary_path actual

  platform="$(detect_platform)"
  mkdir -p "$download_dir"

  if [ -z "$target" ] || [ "$target" = "latest" ] || [ "$target" = "stable" ]; then
    version="$(download_file "$bucket/${target:-latest}" "")"
  else
    version="$target"
  fi

  manifest_json="$(download_file "$bucket/$version/manifest.json" "")"
  checksum="$(get_checksum_from_manifest "$manifest_json" "$platform" || true)"
  if [ -z "$checksum" ] || [[ ! "$checksum" =~ ^[a-f0-9]{64}$ ]]; then
    echo "Platform $platform not found in manifest" >&2
    exit 1
  fi

  binary_path="$download_dir/claude-$version-$platform"
  download_file "$bucket/$version/$platform/claude" "$binary_path"

  if command -v sha256sum >/dev/null 2>&1; then
    actual="$(sha256sum "$binary_path" | cut -d' ' -f1)"
  elif command -v shasum >/dev/null 2>&1; then
    actual="$(shasum -a 256 "$binary_path" | cut -d' ' -f1)"
  else
    echo "sha256sum or shasum is required" >&2
    rm -f "$binary_path"
    exit 1
  fi

  if [ "$actual" != "$checksum" ]; then
    echo "Checksum verification failed" >&2
    rm -f "$binary_path"
    exit 1
  fi

  chmod +x "$binary_path"
  mkdir -p "$HOME/.local/bin"
  cp "$binary_path" "$HOME/.local/bin/claude"
  rm -f "$binary_path"
}

CLAUDE_TARGET="$(resolve_requested_target || true)"
CLAUDE_PLATFORM="$(detect_platform)"
CLAUDE_ALT_PLATFORM=""
CLAUDE_CANDIDATE_BINARY="$(resolve_offline_binary "$CLAUDE_OFFLINE_DIR" "$CLAUDE_PLATFORM" "$CLAUDE_TARGET" 2>/dev/null || true)"

if [ -z "$CLAUDE_CANDIDATE_BINARY" ]; then
  if [[ "$CLAUDE_PLATFORM" == linux-*-musl ]]; then
    CLAUDE_ALT_PLATFORM="${CLAUDE_PLATFORM%-musl}"
  elif [[ "$CLAUDE_PLATFORM" == linux-* ]]; then
    CLAUDE_ALT_PLATFORM="${CLAUDE_PLATFORM}-musl"
  fi

  if [ -n "$CLAUDE_ALT_PLATFORM" ]; then
    CLAUDE_CANDIDATE_BINARY="$(resolve_offline_binary "$CLAUDE_OFFLINE_DIR" "$CLAUDE_ALT_PLATFORM" "$CLAUDE_TARGET" 2>/dev/null || true)"
  fi
fi

if [ -n "$CLAUDE_CANDIDATE_BINARY" ]; then
  install_offline_binary "$CLAUDE_CANDIDATE_BINARY"
  if ! validate_installed_claude /tmp/claude-version.log; then
    echo "[claude-install] Validation failed for offline binary: $(basename "$CLAUDE_CANDIDATE_BINARY")" >&2
    exit 1
  fi
else
  if [ "$CLAUDE_REQUIRE_OFFLINE" = "1" ]; then
    echo "[claude-install] Offline install required but no Claude binary was found." >&2
    echo "[claude-install] Looked in: $CLAUDE_OFFLINE_DIR" >&2
    echo "[claude-install] Target: ${CLAUDE_TARGET:-latest}, platform: $CLAUDE_PLATFORM" >&2
    ls -lah "$CLAUDE_OFFLINE_DIR" 2>/dev/null || true
    exit 1
  fi

  echo "[claude-install] Offline binary not found, falling back to direct download."
  install_online_binary "$CLAUDE_TARGET"
  validate_installed_claude /tmp/claude-version.log
fi

grep -q '.local/bin' ~/.bashrc 2>/dev/null || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
export PATH="$HOME/.local/bin:$PATH"
claude --version
"""
        return command.strip().replace("__DEFAULT_VERSION__", default_version)

    async def _installed_claude_satisfies_version(
        self, environment: BaseEnvironment
    ) -> bool:
        # Single-mode pooled runs create a fresh agent object for each task, but
        # reuse the same worker container. After the first task installs Claude
        # Code into that container, later tasks should reuse it instead of
        # paying the install cost again.
        requested_version = (
            self._get_env("CLAUDE_VERSION") or self._version or ""
        ).strip()

        if not requested_version:
            check_result = await environment.exec(command=self._INSTALL_CHECK_COMMAND)
            if check_result.return_code != 0:
                return False
            # Record the detected version when the probe surfaces one so pooled
            # reuse reports the actual installed Claude Code version.
            if check_result.stdout:
                self._version = self.parse_version(check_result.stdout)
            return True

        version_result = await environment.exec(command=self._INSTALL_VERSION_COMMAND)
        if version_result.return_code != 0 or not version_result.stdout:
            return False

        installed_version = self.parse_version(version_result.stdout)
        if installed_version == requested_version:
            self._version = installed_version
            return True

        self.logger.info(
            "Claude Code version mismatch; reinstalling",
            extra={
                "installed_version": installed_version,
                "requested_version": requested_version,
            },
        )
        return False

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        if await self._installed_claude_satisfies_version(environment):
            self.logger.info("Claude Code already available; skipping install")
            return

        # Install system packages (root)
        # Claude Code's node-tree-kill dependency shells out to ps/pgrep when
        # cleaning up process trees, so procps must be present in the image.
        await self.exec_as_root(
            environment,
            command=(
                "if command -v apk &> /dev/null; then"
                "  apk add --no-cache curl wget gcompat libc6-compat libstdc++ procps >/dev/null 2>&1 || "
                '  echo "Warning: apk compatibility package install failed; continuing with existing runtime" >&2;'
                " elif command -v apt-get &> /dev/null; then"
                # 2026-09-17：sandbox 禁网（DNS 锁死）后 apt-get update 必失败；
                # 该安装本是 best-effort（claude 走离线二进制），加容错防
                # exec_as_root 非零抛穿整条 install。
                "  ( apt-get update && apt-get install -y curl procps ) || "
                '  echo "Warning: apt package install failed; continuing" >&2;'
                " elif command -v yum &> /dev/null; then"
                "  yum install -y curl procps-ng;"
                " else"
                '  echo "Skipping root package install for non-apk image" >&2;'
                " fi"
            ),
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        # Install claude-code (as default user)
        await self.exec_as_agent(
            environment,
            command=self._build_install_command(),
        )

    def _get_session_dir(self) -> Path | None:
        """Identify the Claude session directory containing the primary JSONL log"""
        sessions_root = self.logs_dir / "sessions"
        if not sessions_root.exists():
            return None

        project_root = sessions_root / "projects"
        if not project_root.is_dir():
            return None

        # Find project directories that contain .jsonl files at any depth.
        projects_with_sessions: dict[Path, list[Path]] = {}
        for project_dir in project_root.iterdir():
            if not project_dir.is_dir():
                continue
            jsonl_files = list(project_dir.rglob("*.jsonl"))
            if not jsonl_files:
                continue
            session_dirs = list(
                {f.parent for f in jsonl_files if "subagents" not in f.parent.parts}
            )
            if not session_dirs:
                continue
            projects_with_sessions[project_dir] = session_dirs

        if not projects_with_sessions:
            return None

        all_session_dirs = [
            session_dir
            for session_dirs in projects_with_sessions.values()
            for session_dir in session_dirs
        ]

        if len(all_session_dirs) == 1:
            return all_session_dirs[0]

        self.logger.debug(
            "Multiple Claude Code session directories found; "
            "could not identify the correct one"
        )
        return None

    def _convert_event_to_step(self, event: dict[str, Any], step_id: int) -> Step:
        """Convert a normalized Claude Code event dictionary into an ATIF step."""
        kind = event.get("kind")
        timestamp = event.get("timestamp")

        if kind == "message":
            role = event.get("role", "user")
            text = event.get("text", "")
            reasoning = event.get("reasoning")
            metrics = event.get("metrics")
            extra = event.get("extra")
            model_name = event.get("model_name")

            if role == "assistant":
                source = "agent"
            elif role == "user":
                source = "user"
            else:
                source = "system"

            step = Step(
                step_id=step_id,
                timestamp=timestamp,
                source=source,
                message=text,
                llm_call_count=1 if source == "agent" else None,
            )

            if source == "agent":
                if reasoning:
                    step.reasoning_content = reasoning
                if model_name:
                    step.model_name = model_name
                elif self.model_name:
                    step.model_name = self.model_name

            if metrics:
                step.metrics = metrics
            if extra:
                step.extra = extra

            return step

        if kind == "agent_step":
            text = event.get("text") or ""
            reasoning = event.get("reasoning")
            metrics = event.get("metrics")
            extra = event.get("extra")
            model_name = event.get("model_name") or self.model_name
            tool_specs = event.get("tool_calls") or []

            tool_calls: list[ToolCall] = []
            results: list[ObservationResult] = []
            for spec in tool_specs:
                spec_call_id = spec.get("call_id")
                if not spec_call_id:
                    continue
                tool_calls.append(
                    ToolCall(
                        tool_call_id=spec_call_id,
                        function_name=spec.get("tool_name") or "",
                        arguments=spec.get("arguments") or {},
                        extra=spec.get("extra"),
                    )
                )
                if spec.get("output") is not None:
                    results.append(
                        ObservationResult(
                            source_call_id=spec_call_id,
                            content=spec.get("output"),
                            subagent_trajectory_ref=None,
                            extra=spec.get("result_extra"),
                        )
                    )

            step = Step(
                step_id=step_id,
                timestamp=timestamp,
                source="agent",
                message=text,
                tool_calls=tool_calls or None,
                observation=Observation(results=results) if results else None,
                llm_call_count=1,
            )
            if reasoning:
                step.reasoning_content = reasoning
            if model_name:
                step.model_name = model_name
            if metrics:
                step.metrics = metrics
            if extra:
                step.extra = extra

            return step

        if kind == "tool_call":
            call_id = event.get("call_id")
            tool_name = event.get("tool_name")
            if not call_id or not tool_name:
                raise ValueError("Tool call event missing call_id or tool_name")

            arguments = event.get("arguments") or {}
            raw_arguments = event.get("raw_arguments")
            reasoning = event.get("reasoning")
            metrics = event.get("metrics")
            extra = event.get("extra")
            status = event.get("status")
            message = event.get("message")
            output = event.get("output")
            metadata = event.get("metadata")
            model_name = event.get("model_name") or self.model_name

            tool_call = ToolCall(
                tool_call_id=call_id,
                function_name=tool_name,
                arguments=arguments,
            )

            observation_result = ObservationResult(
                source_call_id=call_id,
                content=output,
                subagent_trajectory_ref=None,
            )

            observation = (
                Observation(results=[observation_result])
                if output is not None
                else None
            )

            extra = extra or {}
            updates = {
                "metadata": metadata,
                "raw_arguments": raw_arguments,
                "status": status,
            }
            for key, value in updates.items():
                if value is not None:
                    extra.setdefault(key, value)

            step = Step(
                step_id=step_id,
                timestamp=timestamp,
                source="agent",
                message=message or "",
                tool_calls=[tool_call],
                observation=observation,
                llm_call_count=1,
            )

            if model_name:
                step.model_name = model_name
            if reasoning:
                step.reasoning_content = reasoning
            if metrics:
                step.metrics = metrics
            if extra:
                step.extra = extra

            return step

        raise ValueError(f"Unsupported event kind '{kind}'")

    @staticmethod
    def _stringify(value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False)
        except TypeError:
            return str(value)

    @classmethod
    def _extract_text_reasoning_tool_uses(
        cls, content: Any
    ) -> tuple[str, str | None, list[dict[str, Any]]]:
        if isinstance(content, str):
            text = content.strip()
            return text, None, []

        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_blocks: list[dict[str, Any]] = []

        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    text_parts.append(cls._stringify(block))
                    continue

                block_type = block.get("type")
                if block_type == "tool_use":
                    tool_blocks.append(block)
                    continue

                if block_type in {"thinking", "reasoning", "analysis"}:
                    text_value = (
                        block.get("text")
                        if block.get("text") is not None
                        else block.get("thinking")
                    )
                    if isinstance(text_value, str):
                        reasoning_parts.append(text_value.strip())
                    else:
                        reasoning_parts.append(cls._stringify(text_value))
                    continue

                if block_type == "redacted_thinking":
                    # Anthropic `redacted_thinking` blocks carry encrypted
                    # reasoning in `data` that clients cannot decrypt — they
                    # are meant to be passed back unchanged on subsequent API
                    # calls. OpenRouter, however, mis-uses this envelope to
                    # pass through PLAIN reasoning from non-Anthropic models
                    # routed through their proxy: the `data` field is
                    # `openrouter.reasoning:<b64>`, where the base64 decodes
                    # to plain JSON `{"text": "…", "type": "reasoning.text"}`.
                    # Detect that shape and surface the inner text as
                    # reasoning. For genuine Anthropic ciphertext we drop the
                    # block rather than dump the raw envelope into the
                    # human-readable message field.
                    data = block.get("data")
                    if isinstance(data, str) and data.startswith(
                        "openrouter.reasoning:"
                    ):
                        try:
                            payload = data[len("openrouter.reasoning:") :]
                            decoded = base64.b64decode(payload + "==").decode(
                                "utf-8", "replace"
                            )
                            inner = json.loads(decoded)
                            inner_text = inner.get("text")
                            if isinstance(inner_text, str):
                                reasoning_parts.append(inner_text.strip())
                        except (ValueError, json.JSONDecodeError):
                            pass
                    continue

                if block_type == "code" and isinstance(block.get("code"), str):
                    text_parts.append(block["code"])
                    continue

                text_value = block.get("text")
                if isinstance(text_value, str):
                    text_parts.append(text_value)
                else:
                    text_parts.append(cls._stringify(block))
        elif content is not None:
            text_parts.append(cls._stringify(content))

        text = "\n\n".join(
            part.strip() for part in text_parts if part and str(part).strip()
        )
        reasoning = "\n\n".join(
            part.strip() for part in reasoning_parts if part and str(part).strip()
        )

        return text, (reasoning or None), tool_blocks

    @staticmethod
    def _build_metrics(usage: Any) -> Metrics | None:
        if not isinstance(usage, dict):
            return None

        # Use ``or 0`` rather than a ``get`` default: a streaming response that is
        # interrupted (e.g. the agent is killed on timeout) can leave a usage field
        # present but ``None`` (``"output_tokens": null``), and ``get(key, 0)`` only
        # falls back when the key is absent. Without this guard the arithmetic below
        # raises ``TypeError: int + NoneType``, which aborts the whole trajectory
        # conversion and discards token accounting for the entire trial.
        cached_tokens = usage.get("cache_read_input_tokens") or 0
        creation = usage.get("cache_creation_input_tokens") or 0
        input_tokens = usage.get("input_tokens") or 0
        # Align with Anthropic session totals: input + cache read + cache creation.
        prompt_tokens = input_tokens + cached_tokens + creation
        completion_tokens = usage.get("output_tokens") or 0

        extra: dict[str, Any] = {}
        for key, value in usage.items():
            if key in {"input_tokens", "output_tokens"}:
                continue
            extra[key] = value

        if (
            prompt_tokens is None
            and completion_tokens is None
            and cached_tokens is None
            and not extra
        ):
            return None

        return Metrics(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
            cost_usd=None,
            extra=extra or None,
        )

    @classmethod
    def _format_tool_result(
        cls, block: dict[str, Any], tool_use_result: dict[str, Any] | None
    ) -> tuple[str | None, dict[str, Any] | None]:
        parts: list[str] = []

        content = block.get("content")
        if isinstance(content, str):
            if content.strip():
                parts.append(content.strip())
        elif isinstance(content, list):
            for item in content:
                text_value = cls._stringify(item)
                if text_value.strip():
                    parts.append(text_value.strip())
        elif content not in (None, ""):
            parts.append(cls._stringify(content))

        metadata: dict[str, Any] | None = None
        if tool_use_result and isinstance(tool_use_result, dict):
            metadata = {"tool_use_result": tool_use_result}
            stdout = tool_use_result.get("stdout")
            stderr = tool_use_result.get("stderr")
            exit_code = tool_use_result.get("exitCode") or tool_use_result.get(
                "exit_code"
            )
            interrupted = tool_use_result.get("interrupted")
            is_image = tool_use_result.get("isImage")

            formatted_chunks: list[str] = []
            if stdout:
                formatted_chunks.append(f"[stdout]\n{stdout}".rstrip())
            if stderr:
                formatted_chunks.append(f"[stderr]\n{stderr}".rstrip())
            if exit_code not in (None, 0):
                formatted_chunks.append(f"[exit_code] {exit_code}")
            if interrupted:
                formatted_chunks.append(f"[interrupted] {interrupted}")
            if is_image:
                formatted_chunks.append(f"[is_image] {is_image}")

            remaining_meta = {
                key: value
                for key, value in tool_use_result.items()
                if key
                not in {
                    "stdout",
                    "stderr",
                    "exitCode",
                    "exit_code",
                    "interrupted",
                    "isImage",
                }
            }
            if remaining_meta:
                formatted_chunks.append(
                    f"[metadata] {json.dumps(remaining_meta, ensure_ascii=False)}"
                )

            if formatted_chunks:
                parts.append("\n".join(chunk for chunk in formatted_chunks if chunk))

        if block.get("is_error") is True:
            parts.append("[error] tool reported failure")
            metadata = metadata or {}
            metadata["is_error"] = True

        if metadata is not None:
            metadata.setdefault("raw_tool_result", block)

        result_text = "\n\n".join(part for part in parts if part).strip()
        return (result_text or None), metadata

    def _parse_total_cost_from_stream_json(self) -> float | None:
        """Extract authoritative `total_cost_usd` from Claude Code's stdout stream.

        Claude Code's `--output-format=stream-json --print` mode emits a final
        ``{"type":"result", ..., "total_cost_usd": <float>, ...}`` line to stdout,
        which Harbor tees to ``<logs_dir>/claude-code.txt``. Returns ``None`` if
        the file is missing, malformed, or the result event lacks the field.
        """
        stream_path = self.logs_dir / "claude-code.txt"
        try:
            content = stream_path.read_text(encoding="utf-8")
        except OSError:
            return None
        for line in content.splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "result":
                cost = event.get("total_cost_usd")
                if cost is None:
                    return None
                try:
                    return float(cost)
                except (TypeError, ValueError):
                    return None
        return None

    def _estimate_total_cost_from_steps(self, steps: list[Step]) -> float | None:
        """Estimate cost from transcript usage when Claude omits its result event."""
        try:
            import litellm
        except ImportError:
            self.logger.debug("LiteLLM is unavailable; cannot estimate Claude cost")
            return None

        total_cost = 0.0
        priced_any_step = False
        for step in steps:
            metrics = step.metrics
            if metrics is None:
                continue

            prompt_tokens = metrics.prompt_tokens or 0
            completion_tokens = metrics.completion_tokens or 0
            if prompt_tokens <= 0 and completion_tokens <= 0:
                continue
            if not step.model_name:
                self.logger.debug("Cannot estimate Claude cost without a step model")
                return None

            extra = metrics.extra or {}
            cache_creation_tokens = extra.get("cache_creation_input_tokens")
            if not isinstance(cache_creation_tokens, int):
                cache_creation_tokens = 0
            cache_read_tokens = extra.get("cache_read_input_tokens")
            if not isinstance(cache_read_tokens, int):
                cache_read_tokens = metrics.cached_tokens or 0
            service_tier = extra.get("service_tier")
            if not isinstance(service_tier, str):
                service_tier = None

            try:
                prompt_cost, completion_cost = litellm.cost_per_token(
                    model=step.model_name,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cache_creation_input_tokens=cache_creation_tokens,
                    cache_read_input_tokens=cache_read_tokens,
                    service_tier=service_tier,
                )
            except Exception as exc:
                self.logger.debug(
                    "Cannot estimate Claude cost for model '%s': %s",
                    step.model_name,
                    exc,
                )
                return None

            total_cost += prompt_cost + completion_cost
            priced_any_step = True

        return total_cost if priced_any_step else None

    @staticmethod
    def _first_event_model(
        events: list[dict[str, Any]], *, include_sidechain: bool
    ) -> str | None:
        for event in events:
            if not include_sidechain and event.get("isSidechain"):
                continue
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            model_name = message.get("model")
            if isinstance(model_name, str) and model_name:
                return model_name
        return None

    def _convert_events_to_trajectory(self, session_dir: Path) -> Trajectory | None:
        """Convert Claude session into an ATIF trajectory."""
        # Newer Claude Code versions write each subagent's transcript to its
        # own JSONL under a `subagents/` subdirectory (e.g.
        # `<session-id>/subagents/agent-<id>.jsonl`) instead of inlining
        # sidechain events in the main session file; read both.
        session_files = sorted(session_dir.glob("*.jsonl")) + sorted(
            session_dir.rglob("subagents/*.jsonl")
        )

        if not session_files:
            self.logger.debug(f"No Claude Code session files found in {session_dir}")
            return None

        raw_events: list[dict[str, Any]] = []
        for session_file in session_files:
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

        seen_event_uuids: set[str] = set()
        deduped_raw_events: list[dict[str, Any]] = []
        for event in raw_events:
            uuid = event.get("uuid")
            if isinstance(uuid, str) and uuid:
                if uuid in seen_event_uuids:
                    self.logger.debug(
                        f"Skipping duplicate Claude Code session event {uuid}"
                    )
                    continue
                seen_event_uuids.add(uuid)
            deduped_raw_events.append(event)
        raw_events = deduped_raw_events

        # Keep events in chronological order across the main chain and any
        # subagent sidechains, so the first user step remains the instruction
        # (downstream byte-identity checks rely on this) and step timestamps
        # stay monotonic; sidechain steps are marked via `extra.is_sidechain`.
        raw_events.sort(key=lambda e: e.get("timestamp", ""))
        events = raw_events

        session_id: str = session_dir.name
        for event in events:
            sid = event.get("sessionId")
            if isinstance(sid, str):
                session_id = sid
                break

        agent_version: str = "unknown"
        for event in events:
            ver = event.get("version")
            if isinstance(ver, str) and ver:
                agent_version = ver
                break

        cwds = {
            event.get("cwd")
            for event in events
            if isinstance(event.get("cwd"), str) and event.get("cwd")
        }
        git_branches = {
            event.get("gitBranch")
            for event in events
            if isinstance(event.get("gitBranch"), str) and event.get("gitBranch")
        }
        agent_ids = {
            event.get("agentId")
            for event in events
            if isinstance(event.get("agentId"), str) and event.get("agentId")
        }

        agent_extra: dict[str, Any] | None = {}
        if cwds:
            agent_extra["cwds"] = cwds
        if git_branches:
            agent_extra["git_branches"] = git_branches
        if agent_ids:
            agent_extra["agent_ids"] = agent_ids
        if not agent_extra:
            agent_extra = None

        # Prefer the main chain's model so a subagent running a different
        # model (e.g. a small/fast model) cannot become the trajectory's
        # `agent.model_name` or the fallback for model-less steps.
        default_model_name = (
            self._first_event_model(events, include_sidechain=False)
            or self._first_event_model(events, include_sidechain=True)
            or self.model_name
        )

        # Per message id, keep the last usage (streaming updates it on each chunk).
        last_usage_by_msg_id: dict[str, Any] = {}
        for ev in events:
            if ev.get("type") != "assistant":
                continue
            msg = ev.get("message")
            if not isinstance(msg, dict):
                continue
            mid = msg.get("id")
            usage = msg.get("usage")
            if mid and usage is not None:
                last_usage_by_msg_id[mid] = usage

        normalized_events: list[dict[str, Any]] = []
        pending_calls: dict[str, dict[str, Any]] = {}
        completed_call_ids: set[str] = set()
        seen_message_ids: set[str] = set()
        # Maps an assistant `message.id` to the single agent_step it bundles, so
        # text / reasoning / every tool_use from one LLM inference land on one
        # ATIF step even if the session log splits them across events.
        turn_by_msgid: dict[str, dict[str, Any]] = {}

        for event in events:
            message = event.get("message")
            if not isinstance(message, dict):
                continue

            event_type = event.get("type")
            timestamp = event.get("timestamp")

            if event_type == "assistant":
                text, reasoning, tool_blocks = self._extract_text_reasoning_tool_uses(
                    message.get("content")
                )

                msg_id = message.get("id")
                if msg_id and msg_id in seen_message_ids:
                    metrics = None
                else:
                    # Claude-Code accumulates usage, so the "real" usage is the last
                    # message in the chain. We still report this usage on the first
                    # shown in the trajectory.
                    usage = (
                        last_usage_by_msg_id.get(msg_id, message.get("usage"))
                        if msg_id
                        else message.get("usage")
                    )
                    metrics = self._build_metrics(usage)
                    if msg_id:
                        seen_message_ids.add(msg_id)
                extra: dict[str, Any] = {}
                for key in ("stop_reason", "stop_sequence", "requestId"):
                    value = message.get(key)
                    if value is not None:
                        extra[key] = value
                if event.get("id"):
                    extra["id"] = event["id"]
                if event.get("agent_id"):
                    extra["agent_id"] = event["agent_id"]
                if event.get("cwd"):
                    extra.setdefault("cwd", event["cwd"])
                if event.get("userType") and event.get("userType") != "external":
                    extra["user_type"] = event["userType"]
                extra["is_sidechain"] = event.get("isSidechain", False)

                model_name = message.get("model") or default_model_name

                # Bundle one LLM inference (text, reasoning, and all tool_use
                # calls share a `message.id`) into a single ATIF step, per
                # RFC-0001 (`step` == one turn; `tool_calls` is multi-valued).
                # Reuse the turn when the same `message.id` is split across
                # several session-log events.
                turn = turn_by_msgid.get(msg_id) if msg_id else None
                if turn is None:
                    turn = {
                        "kind": "agent_step",
                        "timestamp": timestamp,
                        "role": message.get("role", "assistant"),
                        "text": "",
                        "reasoning": None,
                        "metrics": None,
                        "extra": extra or None,
                        "model_name": model_name,
                        "tool_calls": [],
                    }
                    normalized_events.append(turn)
                    if msg_id:
                        turn_by_msgid[msg_id] = turn

                if text:
                    turn["text"] = (
                        f"{turn['text']}\n\n{text}".strip() if turn["text"] else text
                    )
                if reasoning and message.get("role") == "assistant":
                    turn["reasoning"] = (
                        f"{turn['reasoning']}\n\n{reasoning}"
                        if turn["reasoning"]
                        else reasoning
                    )
                if turn["metrics"] is None and metrics is not None:
                    turn["metrics"] = metrics
                    metrics = None

                turn_calls = turn["tool_calls"]
                if not isinstance(turn_calls, list):
                    turn_calls = []
                    turn["tool_calls"] = turn_calls
                for tool_block in tool_blocks:
                    call_id = tool_block.get("id") or tool_block.get("tool_use_id")
                    if not call_id:
                        continue
                    # A call is keyed by call_id; skip a tool_use already seen
                    # (e.g. an event replayed after compaction) so it is not
                    # bundled twice.
                    if call_id in pending_calls or call_id in completed_call_ids:
                        continue

                    raw_arguments = tool_block.get("input")
                    if isinstance(raw_arguments, dict):
                        arguments = raw_arguments
                    else:
                        arguments = {"input": raw_arguments}

                    call_extra: dict[str, Any] = {}
                    if raw_arguments is not None:
                        call_extra["raw_arguments"] = raw_arguments
                    if tool_block.get("status") is not None:
                        call_extra["status"] = tool_block.get("status")
                    if tool_block.get("is_error") is not None:
                        call_extra["tool_use_is_error"] = tool_block.get("is_error")
                    if tool_block.get("name"):
                        call_extra.setdefault("tool_use_name", tool_block.get("name"))

                    tool_call_spec: dict[str, Any] = {
                        "call_id": call_id,
                        "tool_name": tool_block.get("name") or "",
                        "arguments": arguments or {},
                        "extra": call_extra or None,
                        "output": None,
                        "result_extra": None,
                    }
                    turn_calls.append(tool_call_spec)
                    pending_calls[call_id] = tool_call_spec

                continue

            if event_type == "user":
                content = message.get("content")
                if isinstance(content, str):
                    # Preserve the raw bytes of the user message so that
                    # downstream byte-identity checks (e.g. sha256 of the
                    # canonical instruction.md vs the first user step) hold;
                    # `if text.strip()` still drops empty / whitespace-only
                    # entries to match the previous skip semantics. The
                    # `text` truthy check is redundant — an empty str
                    # also strips to an empty str, which is falsy — so a
                    # single `text.strip()` does both jobs.
                    text = content
                    if text.strip():
                        extra = {"is_sidechain": event.get("isSidechain", False)}
                        normalized_events.append(
                            {
                                "kind": "message",
                                "timestamp": timestamp,
                                "role": "user",
                                "text": text,
                                "extra": extra,
                            }
                        )
                    continue

                if isinstance(content, list):
                    text_parts: list[str] = []
                    for block in content:
                        # Byte-faithful: when a block is a plain text
                        # content block (`{"type": "text", "text": "..."}`),
                        # use the inner string verbatim instead of letting
                        # `_stringify` json-encode the whole dict. This
                        # keeps trailing/internal whitespace intact for
                        # downstream sha256-of-user-message checks.
                        if (
                            isinstance(block, dict)
                            and block.get("type") == "text"
                            and isinstance(block.get("text"), str)
                        ):
                            text_parts.append(block["text"])
                            continue

                        if (
                            isinstance(block, dict)
                            and block.get("type") == "tool_result"
                        ):
                            call_id = block.get("tool_use_id")
                            formatted_output, metadata = self._format_tool_result(
                                block, event.get("toolUseResult")
                            )
                            call_info = (
                                pending_calls.pop(call_id, None) if call_id else None
                            )
                            if call_info is not None:
                                # Matched a pending tool call: attach the result
                                # in place on its bundled turn (already appended
                                # to normalized_events as one agent_step).
                                result_extra: dict[str, Any] = {}
                                if metadata:
                                    result_extra["tool_result_metadata"] = metadata
                                if block.get("is_error") is not None:
                                    result_extra["tool_result_is_error"] = block.get(
                                        "is_error"
                                    )
                                call_info["output"] = formatted_output
                                call_info["result_extra"] = result_extra or None
                                if call_id:
                                    completed_call_ids.add(call_id)
                                continue

                            # Orphan tool_result with no matching tool_use in
                            # this window (e.g. replayed after compaction): keep
                            # the legacy single-call handling so its output is
                            # not lost. A duplicate of an already-completed call
                            # is dropped; a result with no tool name is skipped.
                            if call_id and call_id in completed_call_ids:
                                self.logger.debug(
                                    f"Skipping duplicate Claude Code tool result {call_id}"
                                )
                                continue
                            tool_name = (
                                block.get("name") or block.get("tool_name") or ""
                            )
                            if not tool_name:
                                self.logger.debug(
                                    "Skipping orphan Claude Code tool result "
                                    f"{call_id or '<missing>'} without tool name"
                                )
                                continue
                            call_info = {
                                "kind": "tool_call",
                                "timestamp": timestamp,
                                "call_id": call_id or "",
                                "tool_name": tool_name,
                                "arguments": {},
                                "raw_arguments": None,
                                "reasoning": None,
                                "status": None,
                                "message": None,
                                "extra": None,
                                "metrics": None,
                                "model_name": default_model_name,
                            }

                            extra_val = call_info.get("extra")
                            extra = extra_val if isinstance(extra_val, dict) else {}
                            extra["is_sidechain"] = event.get("isSidechain", False)
                            if metadata:
                                extra.setdefault("tool_result_metadata", metadata)
                            if block.get("is_error") is not None:
                                extra.setdefault(
                                    "tool_result_is_error", block.get("is_error")
                                )

                            call_info["extra"] = extra or None
                            call_info["output"] = formatted_output
                            call_info["metadata"] = metadata
                            call_info["timestamp"] = (
                                call_info.get("timestamp") or timestamp
                            )
                            call_info.setdefault("model_name", default_model_name)

                            normalized_events.append(call_info)
                            if call_id:
                                completed_call_ids.add(call_id)
                            continue

                        # Anthropic content blocks that aren't tool_result
                        # (notably {"type":"text","text":"…"} text blocks
                        # injected as user content, e.g. when Claude Code
                        # loads a Skill) should have their inner string
                        # surfaced, not the whole block dict JSON-encoded.
                        # Without this, the ATIF user message ends up as a
                        # raw envelope like
                        #   '{"type":"text","text":"Base directory for…"}'
                        # which downstream renderers can't read as message
                        # text.
                        if (
                            isinstance(block, dict)
                            and block.get("type") == "text"
                            and isinstance(block.get("text"), str)
                        ):
                            text_parts.append(block["text"])
                            continue

                        text_parts.append(self._stringify(block))

                    # Drop the per-part `.strip()` so byte spans inside each
                    # part round-trip; still filter parts that are empty or
                    # whitespace-only so the join doesn't materialise
                    # `\n\n` separators between nothing. ``part`` is always
                    # the output of `block["text"]` or `_stringify(block)`,
                    # both of which return ``str``, so a plain
                    # ``part.strip()`` is the single sufficient filter.
                    text_message = "\n\n".join(
                        part for part in text_parts if part.strip()
                    )
                    if text_message:
                        normalized_events.append(
                            {
                                "kind": "message",
                                "timestamp": timestamp,
                                "role": "user",
                                "text": text_message,
                                "extra": {
                                    "is_sidechain": event.get("isSidechain", False)
                                },
                            }
                        )
                    continue

                if content not in (None, ""):
                    # Same byte-faithful rule as above: keep the raw bytes
                    # of the stringified content and use `.strip()` only
                    # for the empty-skip filter, not as a transform.
                    text = self._stringify(content)
                    if text.strip():
                        normalized_events.append(
                            {
                                "kind": "message",
                                "timestamp": timestamp,
                                "role": "user",
                                "text": text,
                                "extra": {
                                    "is_sidechain": event.get("isSidechain", False)
                                },
                            }
                        )

        # Leftover pending calls (a tool_use that never received a result) are
        # already embedded in their bundled turn's `tool_calls`, so there is
        # nothing to flush here; they render as a ToolCall with no observation.

        steps: list[Step] = []
        for norm_event in normalized_events:
            try:
                step = self._convert_event_to_step(norm_event, len(steps) + 1)
            except ValueError as exc:
                self.logger.debug(f"Skipping event during step conversion: {exc}")
                continue

            if step.source == "agent" and not step.model_name and default_model_name:
                step.model_name = default_model_name

            steps.append(step)

        if not steps:
            self.logger.debug("No valid steps produced from Claude Code session")
            return None

        prompt_values = [
            step.metrics.prompt_tokens
            for step in steps
            if step.metrics and step.metrics.prompt_tokens is not None
        ]
        completion_values = [
            step.metrics.completion_tokens
            for step in steps
            if step.metrics and step.metrics.completion_tokens is not None
        ]
        cached_values = [
            step.metrics.cached_tokens
            for step in steps
            if step.metrics and step.metrics.cached_tokens is not None
        ]

        total_prompt_tokens = sum(prompt_values) if prompt_values else None
        total_completion_tokens = sum(completion_values) if completion_values else None
        total_cached_tokens = sum(cached_values) if cached_values else None

        service_tiers: set[str] = set()
        cache_creation_total, cache_read_total = 0, 0
        cache_creation_seen, cache_read_seen = False, False
        for step in steps:
            if not step.metrics or not step.metrics.extra:
                continue
            extra = step.metrics.extra
            tier = extra.get("service_tier")
            if isinstance(tier, str):
                service_tiers.add(tier)
            cache_creation = extra.get("cache_creation_input_tokens")
            if isinstance(cache_creation, int):
                cache_creation_total += cache_creation
                cache_creation_seen = True
            cache_read = extra.get("cache_read_input_tokens")
            if isinstance(cache_read, int):
                cache_read_total += cache_read
                cache_read_seen = True

        final_extra: dict[str, Any] = {}
        if service_tiers:
            final_extra["service_tiers"] = sorted(service_tiers)
        if cache_creation_seen:
            final_extra["total_cache_creation_input_tokens"] = cache_creation_total
        if cache_read_seen:
            final_extra["total_cache_read_input_tokens"] = cache_read_total

        total_cost_usd = self._parse_total_cost_from_stream_json()
        if total_cost_usd is None:
            total_cost_usd = self._estimate_total_cost_from_steps(steps)
            if total_cost_usd is not None:
                final_extra["cost_source"] = "litellm_estimate"

        final_metrics = FinalMetrics(
            total_prompt_tokens=total_prompt_tokens,
            total_completion_tokens=total_completion_tokens,
            total_cached_tokens=total_cached_tokens,
            total_cost_usd=total_cost_usd,
            total_steps=len(steps),
            extra=final_extra or None,
        )

        trajectory = Trajectory(
            schema_version="ATIF-v1.7",
            session_id=session_id,
            agent=Agent(
                name=AgentName.CLAUDE_CODE.value,
                version=agent_version,
                model_name=default_model_name,
                extra=agent_extra,
            ),
            steps=steps,
            final_metrics=final_metrics,
        )

        return trajectory

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        session_dir = self._get_session_dir()
        if not session_dir:
            self.logger.debug("No Claude Code session directory found")
            return

        try:
            trajectory = self._convert_events_to_trajectory(session_dir)
        except Exception as exc:
            self.logger.debug(
                f"Failed to convert Claude Code events to trajectory: {exc}"
            )
            return
        if not trajectory:
            self.logger.debug("Failed to convert Claude Code session to trajectory")
            return

        trajectory_path = self.logs_dir / "trajectory.json"
        try:
            with open(trajectory_path, "w", encoding="utf-8") as handle:
                json.dump(
                    trajectory.to_json_dict(), handle, indent=2, ensure_ascii=False
                )
            self.logger.debug(f"Wrote Claude Code trajectory to {trajectory_path}")
        except OSError as exc:
            self.logger.debug(
                f"Failed to write trajectory file {trajectory_path}: {exc}"
            )

        traj_log_path = self.logs_dir / "traj_log.json"
        try:
            traj_log_payload = self._convert_trajectory_to_traj_log(trajectory)
            with open(traj_log_path, "w", encoding="utf-8") as handle:
                json.dump(traj_log_payload, handle, indent=2, ensure_ascii=False)
            self.logger.debug(f"Wrote Toolathlon traj_log to {traj_log_path}")
        except OSError as exc:
            self.logger.debug(f"Failed to write traj_log file {traj_log_path}: {exc}")

        if trajectory.final_metrics:
            metrics = trajectory.final_metrics
            context.cost_usd = metrics.total_cost_usd
            context.n_input_tokens = metrics.total_prompt_tokens or 0
            context.n_cache_tokens = metrics.total_cached_tokens or 0
            context.n_output_tokens = metrics.total_completion_tokens or 0

    def _convert_trajectory_to_traj_log(self, trajectory: Trajectory) -> dict[str, Any]:
        """Convert Harbor ATIF trajectory into a Toolathlon-compatible traj_log payload."""

        def _normalize_content(value: Any) -> str:
            if value is None:
                return ""
            if isinstance(value, str):
                return value
            return self._stringify(value)

        def _tool_call_record(tool_call: Any) -> dict[str, Any]:
            return {
                "id": tool_call.tool_call_id,
                "tool_use_id": tool_call.tool_call_id,
                "type": "function",
                "function": {
                    "name": tool_call.function_name,
                    "arguments": json.dumps(tool_call.arguments, ensure_ascii=False),
                },
            }

        messages: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        agent_turns = 0
        user_turns = 0

        for step in trajectory.steps:
            content = _normalize_content(step.message).strip()
            message_role = "assistant" if step.source == "agent" else step.source
            base_entry: dict[str, Any] = {
                "role": message_role,
                "content": content,
                "step_id": step.step_id,
                "timestamp": step.timestamp,
            }
            if step.extra:
                base_entry["extra"] = step.extra

            if step.source == "agent":
                agent_turns += 1
                if step.model_name:
                    base_entry["model_name"] = step.model_name
                if step.reasoning_content:
                    base_entry["reasoning_content"] = step.reasoning_content
                if step.tool_calls:
                    serialized_calls = [
                        _tool_call_record(call) for call in step.tool_calls
                    ]
                    base_entry["tool_calls"] = serialized_calls
                    tool_calls.extend(serialized_calls)
                messages.append(base_entry)

                if step.observation and step.observation.results:
                    for observation_result in step.observation.results:
                        tool_message: dict[str, Any] = {
                            "role": "tool",
                            "content": _normalize_content(
                                observation_result.content
                            ).strip(),
                            "step_id": step.step_id,
                            "timestamp": step.timestamp,
                        }
                        if observation_result.source_call_id:
                            tool_message["tool_use_id"] = (
                                observation_result.source_call_id
                            )
                        if observation_result.subagent_trajectory_ref:
                            tool_message["subagent_trajectory_ref"] = (
                                observation_result.subagent_trajectory_ref
                            )
                        messages.append(tool_message)
                continue

            if step.source == "user":
                user_turns += 1
            messages.append(base_entry)

        final_metrics = trajectory.final_metrics
        prompt_tokens = final_metrics.total_prompt_tokens if final_metrics else 0
        completion_tokens = (
            final_metrics.total_completion_tokens if final_metrics else 0
        )
        cached_tokens = final_metrics.total_cached_tokens if final_metrics else 0
        total_steps = (
            final_metrics.total_steps
            if final_metrics and final_metrics.total_steps is not None
            else len(trajectory.steps)
        )

        initial_time = trajectory.steps[
            0
        ].timestamp or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        completion_time = trajectory.steps[-1].timestamp or initial_time

        return {
            "config": {
                "source": "harbor",
                "agent": trajectory.agent.model_dump(exclude_none=True, mode="json"),
            },
            "request_id": str(uuid.uuid4()),
            "initial_run_time": initial_time,
            "completion_time": completion_time,
            "tool_calls": {
                "tools": tool_calls,
                "tool_choice": None,
            },
            "status": "success",
            "messages": messages,
            "key_stats": {
                "interaction_turns": user_turns,
                "total_turns": user_turns,
                "agent_llm_requests": agent_turns,
                "tool_calls": len(tool_calls),
                "input_tokens": prompt_tokens or 0,
                "output_tokens": completion_tokens or 0,
                "total_tokens": (prompt_tokens or 0) + (completion_tokens or 0),
                "cached_tokens": cached_tokens or 0,
                "sdk_num_turns": agent_turns,
            },
            "agent_cost": {
                "total_cost": final_metrics.total_cost_usd if final_metrics else 0,
                "total_input_tokens": prompt_tokens or 0,
                "total_output_tokens": completion_tokens or 0,
                "total_requests": agent_turns,
            },
            "user_cost": {
                "total_cost": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_requests": 0,
            },
            "resumed": False,
            "session_id": trajectory.session_id,
            "history_file": None,
            "termination_reason": "harbor_trajectory_exported",
            "final_metrics": (
                final_metrics.model_dump(exclude_none=True, mode="json")
                if final_metrics
                else None
            ),
            "total_steps": total_steps,
        }

    def _build_register_skills_command(self) -> str | None:
        """Return a shell command that copies skills from the environment to Claude's config.

        Copies the contents of ``self.skills_dir`` into
        ``$CLAUDE_CONFIG_DIR/skills/`` so that Claude Code picks them up
        automatically.
        """
        if not self.skills_dir:
            return None
        return (
            f"(cp -r {shlex.quote(self.skills_dir)}/* "
            "$CLAUDE_CONFIG_DIR/skills/ 2>/dev/null || true)"
        )

    def _build_register_memory_command(self) -> str | None:
        """Return a shell command that copies memory files into Claude's config.

        Copies the contents of ``self.memory_dir`` into
        ``$CLAUDE_CONFIG_DIR/projects/-app/memory/`` so that Claude Code
        picks them up as pre-seeded auto-memory at session start.
        """
        if not self.memory_dir:
            return None
        return (
            f"(mkdir -p $CLAUDE_CONFIG_DIR/projects/-app/memory && "
            f"cp -r {shlex.quote(self.memory_dir)}/* "
            "$CLAUDE_CONFIG_DIR/projects/-app/memory/ 2>/dev/null || true)"
        )

    def _build_register_mcp_servers_command(self) -> str | None:
        """Return a shell command that writes MCP config to ~/.claude.json.

        User-scoped servers are loaded without a trust dialog, unlike
        project-scoped .mcp.json which requires explicit enablement.
        """
        if not self.mcp_servers:
            return None
        servers: dict[str, dict[str, Any]] = {}
        for server in self.mcp_servers:
            if server.transport == "stdio":
                servers[server.name] = {
                    "type": "stdio",
                    "command": server.command,
                    "args": server.args,
                }
            else:
                # Claude Code uses "http" for streamable-http transport
                transport = (
                    "http"
                    if server.transport == "streamable-http"
                    else server.transport
                )
                servers[server.name] = {"type": transport, "url": server.url}
        claude_json = json.dumps({"mcpServers": servers}, indent=2)
        escaped = shlex.quote(claude_json)
        return f"echo {escaped} > $CLAUDE_CONFIG_DIR/.claude.json"

    def _use_native_mcp_tool_output_hook(self) -> bool:
        return True

    @staticmethod
    def _native_mcp_tool_output_hook_path() -> str:
        return "$CLAUDE_CONFIG_DIR/hooks/harbor_native_mcp_tool_output.py"

    @staticmethod
    def _native_mcp_tool_output_settings_path() -> str:
        return "$CLAUDE_CONFIG_DIR/harbor-native-mcp-tool-output-settings.json"

    @staticmethod
    def _native_mcp_tool_output_hook_script() -> str:
        return r"""#!/usr/bin/env python3
import json
import sys

TARGET_TOOLS = {"mcp__web__WebSearch", "mcp__web__WebFetch"}


def single_text_block_text(value):
    if isinstance(value, dict) and "content" in value:
        value = value["content"]
    if not isinstance(value, list) or len(value) != 1:
        return None
    block = value[0]
    if not isinstance(block, dict):
        return None
    if block.get("type") != "text":
        return None
    text = block.get("text")
    return text if isinstance(text, str) else None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print("{}")
        return

    if payload.get("hook_event_name") != "PostToolUse":
        print("{}")
        return
    if payload.get("tool_name") not in TARGET_TOOLS:
        print("{}")
        return

    text = single_text_block_text(payload.get("tool_response"))
    if text is None:
        print("{}")
        return

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": text,
            "updatedMCPToolOutput": text,
        }
    }, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
"""

    @classmethod
    def _native_mcp_tool_output_hook_settings(cls) -> dict[str, Any]:
        return {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "mcp__web__WebSearch|mcp__web__WebFetch",
                        "hooks": [
                            {
                                "type": "command",
                                "command": (
                                    'python3 "'
                                    + cls._native_mcp_tool_output_hook_path()
                                    + '"'
                                ),
                            }
                        ],
                    }
                ]
            }
        }

    def _build_native_mcp_tool_output_hook_command(self) -> str | None:
        if not self._use_native_mcp_tool_output_hook():
            return None

        hook_path = self._native_mcp_tool_output_hook_path()
        settings_path = self._native_mcp_tool_output_settings_path()
        hook_script = self._native_mcp_tool_output_hook_script()
        settings_json = json.dumps(
            self._native_mcp_tool_output_hook_settings(),
            ensure_ascii=False,
            indent=2,
        )
        return (
            'mkdir -p "$CLAUDE_CONFIG_DIR/hooks" && '
            f"cat > \"{hook_path}\" <<'PY'\n"
            f"{hook_script}"
            "PY\n"
            f'chmod +x "{hook_path}" && '
            f"cat > \"{settings_path}\" <<'JSON'\n"
            f"{settings_json}\n"
            "JSON"
        )

    @staticmethod
    def _is_bedrock_mode() -> bool:
        """Check if Bedrock mode is enabled via environment variables."""
        if os.environ.get("CLAUDE_CODE_USE_BEDROCK", "").strip() == "1":
            return True
        if os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "").strip():
            return True
        return False

    def _is_bedrock_mode_for_agent(self) -> bool:
        """Check Bedrock mode with agent-specific env overrides included."""
        if (self._get_env("CLAUDE_CODE_USE_BEDROCK") or "").strip() == "1":
            return True
        if (self._get_env("AWS_BEARER_TOKEN_BEDROCK") or "").strip():
            return True
        return False

    def _should_force_oauth(self) -> bool:
        """Whether to drop the API key so the CLI uses CLAUDE_CODE_OAUTH_TOKEN.

        Opt in via CLAUDE_FORCE_OAUTH=<truthy>; default keeps ANTHROPIC_API_KEY.
        Mirrors codex's CODEX_FORCE_AUTH_JSON.
        """
        return parse_bool_env_value(
            self._get_env("CLAUDE_FORCE_OAUTH"),
            name="CLAUDE_FORCE_OAUTH",
            default=False,
        )

    def _build_env_vars(self) -> dict[str, str]:
        use_bedrock = self._is_bedrock_mode_for_agent()

        # CLAUDE_FORCE_OAUTH drops the API key so the CLI uses the subscription
        # token; default keeps the key (the CLI prefers it). Mirrors codex. Only
        # the per-call env is dropped, not a key set in extra_env.
        force_oauth = self._should_force_oauth()
        oauth_token = (self._get_env("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()

        if force_oauth and not oauth_token:
            raise RuntimeError(
                "CLAUDE_FORCE_OAUTH is set but CLAUDE_CODE_OAUTH_TOKEN is not. "
                "Run `claude setup-token` to get one, or unset CLAUDE_FORCE_OAUTH."
            )

        if force_oauth:
            api_key = ""
            auth_token = ""
        else:
            api_key = self._get_env("ANTHROPIC_API_KEY") or ""
            auth_token = self._get_env("ANTHROPIC_AUTH_TOKEN") or ""
            if (api_key or auth_token) and oauth_token:
                self.logger.debug(
                    "API key and OAuth token both set; using the API key "
                    "(set CLAUDE_FORCE_OAUTH=1 to use the subscription)."
                )

        # Agent-specific env comes from AgentConfig.env / --agent-env and should
        # override process-wide os.environ. This lets one job or agent point at
        # a different endpoint/key without mutating the whole Harbor process.
        env = {
            # "" when forced, then dropped by the empty-value filter below.
            "ANTHROPIC_API_KEY": api_key,
            "ANTHROPIC_AUTH_TOKEN": auth_token,
            "ANTHROPIC_BASE_URL": self._get_env("ANTHROPIC_BASE_URL"),
            "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": self._get_env(
                "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"
            ),
            "CLAUDE_CODE_OAUTH_TOKEN": oauth_token,
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW": self._get_env(
                "CLAUDE_CODE_AUTO_COMPACT_WINDOW"
            ),
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": self._get_env(
                "CLAUDE_CODE_MAX_OUTPUT_TOKENS"
            ),
            "DISABLE_AUTO_COMPACT": self._get_env("DISABLE_AUTO_COMPACT"),
            "DISABLE_COMPACT": self._get_env("DISABLE_COMPACT"),
            "FORCE_AUTO_BACKGROUND_TASKS": "1",
            "ENABLE_BACKGROUND_TASKS": "1",
        }

        # Bedrock configuration: pass through AWS credentials and region
        if use_bedrock:
            env["CLAUDE_CODE_USE_BEDROCK"] = "1"

            # AWS Bedrock API key auth (Option E from Bedrock docs)
            bedrock_token = self._get_env("AWS_BEARER_TOKEN_BEDROCK") or ""
            if bedrock_token:
                env["AWS_BEARER_TOKEN_BEDROCK"] = bedrock_token

            # Standard AWS credential chain (Option B from Bedrock docs)
            for aws_var in (
                "AWS_ACCESS_KEY_ID",
                "AWS_SECRET_ACCESS_KEY",
                "AWS_SESSION_TOKEN",
                "AWS_PROFILE",
            ):
                val = self._get_env(aws_var) or ""
                if val:
                    env[aws_var] = val

            # AWS_REGION is required for Bedrock; default to us-east-1
            env["AWS_REGION"] = self._get_env("AWS_REGION") or "us-east-1"

            # Optional: separate region for the small/fast model (Haiku)
            small_model_region = self._get_env("ANTHROPIC_SMALL_FAST_MODEL_AWS_REGION")
            if small_model_region:
                env["ANTHROPIC_SMALL_FAST_MODEL_AWS_REGION"] = small_model_region

            # Optional: disable prompt caching (not available in all regions)
            if (self._get_env("DISABLE_PROMPT_CACHING") or "").strip() == "1":
                env["DISABLE_PROMPT_CACHING"] = "1"

        # Remove empty auth credentials to allow Claude CLI to prioritize the available method
        # When both are empty, Claude CLI will fail with a clear authentication error
        env = {k: v for k, v in env.items() if v}

        # Handle model name based on whether using custom API base or Bedrock
        if self.model_name:
            if use_bedrock:
                # Bedrock model IDs (e.g. global.anthropic.claude-sonnet-4-5-20250929-v1:0)
                # or ARNs should be passed through as-is; strip the provider prefix only
                # when it looks like a Harbor-style "provider/model" string
                if "/" in self.model_name:
                    env["ANTHROPIC_MODEL"] = self.model_name.split("/", 1)[-1]
                else:
                    env["ANTHROPIC_MODEL"] = self.model_name
            elif "ANTHROPIC_BASE_URL" in env:
                # If using custom base URL (OpenRouter, self-hosted), keep full model name
                env["ANTHROPIC_MODEL"] = self.model_name
            else:
                # Strip provider prefix for official Anthropic API
                env["ANTHROPIC_MODEL"] = self.model_name.split("/")[-1]
        elif self._get_env("ANTHROPIC_MODEL"):
            env["ANTHROPIC_MODEL"] = self._get_env("ANTHROPIC_MODEL") or ""

        # When using custom base URL, set all model aliases to the same model
        if "ANTHROPIC_BASE_URL" in env and "ANTHROPIC_MODEL" in env:
            env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = env["ANTHROPIC_MODEL"]
            env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = env["ANTHROPIC_MODEL"]
            env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = env["ANTHROPIC_MODEL"]
            env["CLAUDE_CODE_SUBAGENT_MODEL"] = env["ANTHROPIC_MODEL"]

        # Disable adaptive thinking if requested
        if (
            self._get_env("CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING") or ""
        ).strip() == "1":
            env["CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING"] = "1"

        # Disable non-essential traffic (telemetry, etc.)
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"

        # Allow bypassPermissions mode when running as root inside containers
        env["IS_SANDBOX"] = "1"

        # Merge declarative env vars (e.g. MAX_THINKING_TOKENS)
        env.update(self._resolved_env_vars)

        env["CLAUDE_CONFIG_DIR"] = (EnvironmentPaths.agent_dir / "sessions").as_posix()
        return env

    def _build_setup_command(self) -> str:
        setup_command = (
            "mkdir -p $CLAUDE_CONFIG_DIR/debug $CLAUDE_CONFIG_DIR/projects/-app "
            "$CLAUDE_CONFIG_DIR/shell-snapshots $CLAUDE_CONFIG_DIR/statsig "
            "$CLAUDE_CONFIG_DIR/todos $CLAUDE_CONFIG_DIR/skills && "
            "if [ -d ~/.claude/skills ]; then "
            "cp -r ~/.claude/skills/. $CLAUDE_CONFIG_DIR/skills/ 2>/dev/null || true; "
            "fi"
        )

        skills_command = self._build_register_skills_command()
        if skills_command:
            setup_command += f" && {skills_command}"

        memory_command = self._build_register_memory_command()
        if memory_command:
            setup_command += f" && {memory_command}"

        mcp_command = self._build_register_mcp_servers_command()
        if mcp_command:
            setup_command += f" && {mcp_command}"

        native_mcp_hook_command = self._build_native_mcp_tool_output_hook_command()
        if native_mcp_hook_command:
            setup_command += f" && {native_mcp_hook_command}"

        return setup_command

    def _build_run_command(
        self,
        instruction: str,
        *,
        log_path: str = "/logs/agent/claude-code.txt",
        session_id: str | None = None,
        resume: bool = False,
        instruction_file: str | None = None,
    ) -> str:
        cli_flags = self.build_cli_flags()
        extra_flags = (cli_flags + " ") if cli_flags else ""
        settings_flags = (
            f'--settings "{self._native_mcp_tool_output_settings_path()}" '
            if self._use_native_mcp_tool_output_hook()
            else ""
        )
        session_flags = ""
        if session_id:
            session_id = self._normalize_session_id(session_id)
            flag = "--resume" if resume else "--session-id"
            session_flags = f"{flag} {shlex.quote(session_id)} "
        elif self._resume:
            # Base resume() path (no tracked session id): continue the most
            # recent conversation via the CLI's native --continue.
            session_flags = "--continue "

        prompt_input = (
            f"< {shlex.quote(instruction_file)}"
            if instruction_file is not None
            else f"-- {shlex.quote(instruction)} </dev/null"
        )
        return (
            'export PATH="$HOME/.local/bin:$PATH"; '
            f"claude --verbose --output-format=stream-json "
            f"{extra_flags}"
            f"{settings_flags}"
            f"{session_flags}"
            f"--print {prompt_input} 2>&1 | tee {shlex.quote(log_path)}"
        )

    # Linux limits each execve argv string to MAX_ARG_STRLEN (typically 128 KiB).
    # Keep substantial headroom for environment-specific shell wrappers and quoting.
    _MAX_INLINE_COMMAND_BYTES = 64 * 1024

    async def _stage_instruction_file(
        self,
        environment: BaseEnvironment,
        instruction: str,
        *,
        log_path: str = "/logs/agent/claude-code.txt",
        session_id: str | None = None,
        resume: bool = False,
    ) -> str | None:
        inline_command = self._build_run_command(
            instruction,
            log_path=log_path,
            session_id=session_id,
            resume=resume,
        )
        if len(inline_command.encode("utf-8")) <= self._MAX_INLINE_COMMAND_BYTES:
            return None

        remote_path = f"/tmp/harbor-claude-instruction-{uuid.uuid4().hex}.txt"
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".txt",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(instruction)
            local_path = Path(handle.name)

        local_path.chmod(0o644)
        try:
            await environment.upload_file(local_path, remote_path)
        finally:
            local_path.unlink(missing_ok=True)
        return remote_path

    async def _prepare_run_command(
        self,
        environment: BaseEnvironment,
        instruction: str,
        *,
        log_path: str = "/logs/agent/claude-code.txt",
        session_id: str | None = None,
        resume: bool = False,
    ) -> str:
        instruction_file = await self._stage_instruction_file(
            environment,
            instruction,
            log_path=log_path,
            session_id=session_id,
            resume=resume,
        )
        return self._build_run_command(
            instruction,
            log_path=log_path,
            session_id=session_id,
            resume=resume,
            instruction_file=instruction_file,
        )

    @with_prompt_template
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        env = self._build_env_vars()
        setup_command = self._build_setup_command()

        await self.exec_as_agent(environment, command=setup_command, env=env)
        run_command = await self._prepare_run_command(environment, instruction)
        await self.exec_as_agent(environment, command=run_command, env=env)

    async def run_interactive_turn(
        self,
        user_message: str,
        environment: BaseEnvironment,
        context: AgentContext,
        session_id: str,
        turn_idx: int,
    ) -> str:
        env = self._build_env_vars()
        if turn_idx == 0:
            setup_command = self._build_setup_command()
            await self.exec_as_agent(environment, command=setup_command, env=env)

        command_dir = self.logs_dir / f"command-{turn_idx + 1}"
        command_dir.mkdir(parents=True, exist_ok=True)
        command = await self._prepare_run_command(
            environment,
            self.render_instruction(user_message),
            log_path=f"/logs/agent/claude-code-turn-{turn_idx + 1}.txt",
            session_id=session_id,
            resume=turn_idx > 0,
        )
        (command_dir / "command.txt").write_text(command)
        result = await self.exec_as_agent(environment, command=command, env=env)
        (command_dir / "return-code.txt").write_text(str(result.return_code))
        if result.stdout:
            (command_dir / "stdout.txt").write_text(result.stdout)
        if result.stderr:
            (command_dir / "stderr.txt").write_text(result.stderr)

        return result.stdout or result.stderr or ""

    async def run_multi_turn(
        self,
        instructions: list[str],
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        if len(instructions) == 1:
            await self.run(instructions[0], environment, context)
            return

        env = self._build_env_vars()
        setup_command = self._build_setup_command()
        await self.exec_as_agent(environment, command=setup_command, env=env)

        session_id = str(uuid.uuid4())
        for turn_idx, instruction in enumerate(instructions, start=1):
            command_dir = self.logs_dir / f"command-{turn_idx}"
            command_dir.mkdir(parents=True, exist_ok=True)
            command = await self._prepare_run_command(
                environment,
                self.render_instruction(instruction),
                log_path=f"/logs/agent/claude-code-turn-{turn_idx}.txt",
                session_id=session_id,
                resume=turn_idx > 1,
            )
            (command_dir / "command.txt").write_text(command)
            result = await self.exec_as_agent(environment, command=command, env=env)
            (command_dir / "return-code.txt").write_text(str(result.return_code))
            if result.stdout:
                (command_dir / "stdout.txt").write_text(result.stdout)
            if result.stderr:
                (command_dir / "stderr.txt").write_text(result.stderr)

        self.populate_context_post_run(context)

    async def run_dynamic_multi_turn(
        self,
        instruction: str,
        num_turns: int,
        user_model_config: UserModelConfig,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        if num_turns <= 1:
            await self.run(instruction, environment, context)
            return

        from harbor.llms.lite_llm import LiteLLM

        env = self._build_env_vars()
        setup_command = self._build_setup_command()
        await self.exec_as_agent(environment, command=setup_command, env=env)

        user_llm = LiteLLM(
            model_name=user_model_config.model_name,
            temperature=user_model_config.temperature,
            api_base=user_model_config.api_base,
        )

        self._user_model_api_key = user_model_config.api_key
        session_id = str(uuid.uuid4())
        current_instruction = instruction
        conversation_history: list[dict[str, str]] = []

        for turn_idx in range(num_turns):
            command_dir = self.logs_dir / f"command-{turn_idx + 1}"
            command_dir.mkdir(parents=True, exist_ok=True)
            rendered_instruction = self.render_instruction(current_instruction)
            command = await self._prepare_run_command(
                environment,
                rendered_instruction,
                log_path=f"/logs/agent/claude-code-turn-{turn_idx + 1}.txt",
                session_id=session_id,
                resume=turn_idx > 0,
            )
            (command_dir / "command.txt").write_text(command)
            result = await self.exec_as_agent(environment, command=command, env=env)
            (command_dir / "return-code.txt").write_text(str(result.return_code))
            if result.stdout:
                (command_dir / "stdout.txt").write_text(result.stdout)
            if result.stderr:
                (command_dir / "stderr.txt").write_text(result.stderr)

            conversation_history.append(
                {
                    "turn": str(turn_idx + 1),
                    "instruction": current_instruction,
                    "agent_output": result.stdout or result.stderr or "",
                }
            )

            if turn_idx < num_turns - 1:
                current_instruction = await self._generate_next_instruction(
                    user_llm=user_llm,
                    original_instruction=instruction,
                    conversation_history=conversation_history,
                    turn_idx=turn_idx,
                    num_turns=num_turns,
                    max_tokens=user_model_config.max_tokens,
                )

        self.populate_context_post_run(context)

    async def _generate_next_instruction(
        self,
        user_llm: "LiteLLM",
        original_instruction: str,
        conversation_history: list[dict[str, str]],
        turn_idx: int,
        num_turns: int,
        max_tokens: int,
    ) -> str:
        prompt = self._build_user_model_prompt(
            original_instruction=original_instruction,
            conversation_history=conversation_history,
            current_turn=turn_idx + 2,
            total_turns=num_turns,
        )
        response = await user_llm.call(
            prompt=prompt,
            message_history=[{"role": "system", "content": USER_MODEL_SYSTEM_PROMPT}],
            max_tokens=max_tokens,
            api_key=self._user_model_api_key,
        )
        return response.content.strip()

    @staticmethod
    def _build_user_model_prompt(
        original_instruction: str,
        conversation_history: list[dict[str, str]],
        current_turn: int,
        total_turns: int,
    ) -> str:
        parts = [f"## Original Task\n{original_instruction}"]

        for entry in conversation_history:
            parts.append(f"## Turn {entry['turn']} Instruction\n{entry['instruction']}")
            output = entry["agent_output"]
            if len(output) > 50000:
                output = output[:25000] + "\n...[truncated]...\n" + output[-25000:]
            parts.append(f"## Turn {entry['turn']} Agent Output\n{output}")

        parts.append(
            f"## Your Task\nGenerate the instruction for Turn {current_turn} of {total_turns}."
        )
        return "\n\n".join(parts)
