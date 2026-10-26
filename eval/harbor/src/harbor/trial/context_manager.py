from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from harbor.llms.base import ContextLengthExceededError
from harbor.models.task.config import ContextManagementConfig

_CONTEXT_OVERFLOW_PATTERNS = (
    "context length",
    "context window",
    "contextwindowexceedederror",
    "maximum context length",
    "maximum input length",
    "max context length",
    "context too long",
    "context_length_exceeded",
    "prompt is too long",
    "request too large",
    "too many tokens",
    "token limit exceeded",
)
_SESSION_IN_USER_RE = re.compile(r"_session_([0-9a-fA-F-]{16,})")
_UUID_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


def is_context_overflow_error(error: BaseException | str) -> bool:
    """Return whether an exception or captured error text indicates overflow."""
    if isinstance(error, ContextLengthExceededError):
        return True

    if isinstance(error, BaseException):
        message = f"{type(error).__name__}: {error}".lower()
    else:
        message = error.lower()

    return any(pattern in message for pattern in _CONTEXT_OVERFLOW_PATTERNS) or (
        "input token" in message
        and "requested" in message
        and "output token" in message
    )


class ContextManager:
    """Manages overflow recovery state for long-running interactive trials."""

    def __init__(
        self,
        *,
        config: ContextManagementConfig | None,
        original_instruction: str,
        trial_dir: Path | None = None,
        trials_dir: Path | None = None,
    ) -> None:
        self._config = config or ContextManagementConfig()
        self._original_instruction = original_instruction.strip()
        self._reset_events: list[dict[str, Any]] = []
        self._litellm_log_path = self._resolve_litellm_log_path(trial_dir, trials_dir)
        self._max_chars_per_message = max(200, self._config.max_chars_per_message)
        self._litellm_tail_bytes = max(1024 * 1024, self._config.litellm_tail_bytes)

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def reset_count(self) -> int:
        return len(self._reset_events)

    def is_context_overflow_error(self, exc: Exception) -> bool:
        return is_context_overflow_error(exc)

    def can_auto_reset(self, exc: Exception) -> bool:
        return self.reset_count < self._config.max_resets and (
            (
                self.enabled
                and self._config.auto_reset_on_overflow
                and self.is_context_overflow_error(exc)
            )
            or (
                self._config.auto_reset_on_invalid_history
                and re.search(
                    r"Malformed conversation history: messages?\[\d+\]\.content is required",
                    str(exc),
                    re.I,
                )
                is not None
            )
        )

    def register_reset(
        self,
        *,
        base_session_id: str,
        previous_session_id: str,
        reason: str,
    ) -> str:
        reset_idx = self.reset_count + 1
        new_session_id = f"{base_session_id}-ctxreset-{reset_idx}"
        self._reset_events.append(
            {
                "reset_index": reset_idx,
                "previous_session_id": previous_session_id,
                "new_session_id": new_session_id,
                "reason": reason.strip(),
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return new_session_id

    def build_reset_message(
        self,
        *,
        latest_user_message: str,
        session_id: str,
        visible_history: list[dict[str, Any]] | None = None,
    ) -> str:
        if visible_history is not None:
            # 交互评测只能回放已送达的信息，不注入尚未释放的完整instruction。
            users = [entry for entry in visible_history if entry.get("role") == "user"]
            assistants = [
                entry for entry in visible_history if entry.get("role") == "assistant"
            ][-4:]
            original = (
                str(users[0].get("content") or "") if users else latest_user_message
            )
            retained = {id(entry) for entry in assistants}
            history = "\n\n".join(
                f"{entry.get('role')}: {str(entry.get('content') or '')[:8000]}"
                for entry in visible_history
                if (entry.get("role") == "user" and entry is not users[0])
                or id(entry) in retained
            )
        else:
            recent_turns = self._collect_recent_turns_from_litellm(
                session_id=session_id
            )
            history = self._format_recent_turns(recent_turns)
            original = self._original_instruction
        return (
            "[Context Reset Recovery]\n"
            "The previous session could not continue and was reset.\n\n"
            "Original task:\n"
            f"{original}\n\n"
            "Recent completed conversation summary:\n"
            f"{history}\n\n"
            "Latest unfinished user request:\n"
            f"{latest_user_message}\n\n"
            "Continue the task from this point without repeating completed work."
        )

    def build_proactive_reset_message(
        self, *, session_id: str, action: dict[str, Any]
    ) -> str:
        recent_turns = self._collect_recent_turns_from_litellm(session_id=session_id)
        history = self._format_recent_turns(recent_turns)
        action_summary = self._format_proactive_action(action)

        return (
            "[Context Reset Recovery]\n"
            "The agent proactively requested context cleanup, so a fresh session was started.\n\n"
            "Original task:\n"
            f"{self._original_instruction}\n\n"
            "Requested context-management action:\n"
            f"{action_summary}\n\n"
            "Recent completed conversation summary:\n"
            f"{history}\n\n"
            "Continue the task from this point without repeating completed work."
        )

    def build_status_snapshot(
        self,
        *,
        session_id: str,
        started_at: str,
        current_turn: int,
        turns_in_current_sequence: int,
        total_turns_ever: int,
        truncation_history: list[dict[str, Any]],
        history_dir: str = "litellm.jsonl",
    ) -> dict[str, Any]:
        latest_usage = self._collect_latest_usage_from_litellm(session_id=session_id)
        context_limit = latest_usage.get("context_limit", 128000) or 128000
        total_tokens = latest_usage.get("total_tokens", 0) or 0
        usage_percentage = (
            round((total_tokens / context_limit) * 100, 2) if context_limit > 0 else 0.0
        )
        truncated_turns = sum(
            int(item.get("deleted_turns", 0) or 0) for item in truncation_history
        )
        return {
            "session_info": {
                "session_id": session_id,
                "started_at": started_at,
                "history_dir": history_dir,
            },
            "turn_statistics": {
                "current_turn": current_turn,
                "turns_in_current_sequence": turns_in_current_sequence,
                "total_turns_ever": total_turns_ever,
                "truncated_turns": truncated_turns,
            },
            "token_usage": {
                "total_tokens": total_tokens,
                "context_limit": context_limit,
                "usage_percentage": usage_percentage,
                "remaining_tokens": max(0, context_limit - total_tokens),
            },
            "truncation_history": truncation_history,
        }

    def to_metadata(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "auto_reset_on_overflow": self._config.auto_reset_on_overflow,
            "max_resets": self._config.max_resets,
            "summary_recent_turns": self._config.summary_recent_turns,
            "max_chars_per_message": self._max_chars_per_message,
            "litellm_tail_bytes": self._litellm_tail_bytes,
            "litellm_log_path": (
                str(self._litellm_log_path) if self._litellm_log_path else None
            ),
            "reset_count": self.reset_count,
            "reset_events": list(self._reset_events),
        }

    def _resolve_litellm_log_path(
        self, trial_dir: Path | None, trials_dir: Path | None
    ) -> Path | None:
        env_path = os.environ.get("HARBOR_LITELLM_JSONL_PATH", "").strip()
        configured_path = (self._config.litellm_jsonl_path or "").strip()
        candidates: list[Path] = []

        if env_path:
            candidates.append(Path(env_path))
        if configured_path:
            candidates.append(Path(configured_path))

        run_yaml_path = self._find_run_yaml(trial_dir=trial_dir, trials_dir=trials_dir)
        if run_yaml_path is not None:
            inferred = self._extract_litellm_path_from_run_yaml(
                run_yaml_path=run_yaml_path,
                trial_dir=trial_dir,
                trials_dir=trials_dir,
            )
            if inferred is not None:
                candidates.append(inferred)

        if trials_dir is not None:
            candidates.extend(
                sorted(
                    (trials_dir.parent / "logs").glob("litellm*.jsonl"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
            )
        if trial_dir is not None:
            candidates.extend(
                sorted(
                    (trial_dir.parent / "logs").glob("litellm*.jsonl"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
            )

        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    def _find_run_yaml(
        self, *, trial_dir: Path | None, trials_dir: Path | None
    ) -> Path | None:
        roots: list[Path] = []
        if trial_dir is not None:
            roots.append(trial_dir)
        if trials_dir is not None:
            roots.append(trials_dir)

        for root in roots:
            for parent in [root, *root.parents]:
                candidate = parent / "run.yaml"
                if candidate.exists() and candidate.is_file():
                    return candidate
        return None

    def _extract_litellm_path_from_run_yaml(
        self,
        *,
        run_yaml_path: Path,
        trial_dir: Path | None,
        trials_dir: Path | None,
    ) -> Path | None:
        try:
            payload = yaml.safe_load(run_yaml_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return None

        if not isinstance(payload, dict):
            return None
        jobs = payload.get("jobs")
        if not isinstance(jobs, list) or not jobs:
            return None

        job_name = self._infer_job_name_from_paths(
            trial_dir=trial_dir, trials_dir=trials_dir
        )
        preferred_entries: list[dict[str, Any]] = []
        fallback_entries: list[dict[str, Any]] = []

        for item in jobs:
            if not isinstance(item, dict):
                continue
            litellm = item.get("litellm")
            if not isinstance(litellm, dict):
                continue
            log_path = litellm.get("log_jsonl_path")
            if not isinstance(log_path, str) or not log_path.strip():
                continue

            name = item.get("name")
            if job_name and isinstance(name, str) and name == job_name:
                preferred_entries.append(item)
            else:
                fallback_entries.append(item)

        for item in [*preferred_entries, *fallback_entries]:
            litellm = item.get("litellm")
            if not isinstance(litellm, dict):
                continue
            log_path = litellm.get("log_jsonl_path")
            if isinstance(log_path, str) and log_path.strip():
                return Path(log_path.strip())
        return None

    def _infer_job_name_from_paths(
        self, *, trial_dir: Path | None, trials_dir: Path | None
    ) -> str | None:
        for path in (trial_dir, trials_dir):
            if path is None:
                continue
            parts = list(path.parts)
            if "jobs" not in parts:
                continue
            idx = parts.index("jobs")
            if idx + 1 < len(parts):
                return parts[idx + 1]
        return None

    def _collect_recent_turns_from_litellm(
        self, *, session_id: str
    ) -> list[dict[str, str]]:
        log_path = self._litellm_log_path
        if log_path is None:
            return []

        matched_turns: list[dict[str, str]] = []
        for record in self._iter_jsonl_tail(
            log_path, max_bytes=self._litellm_tail_bytes
        ):
            if self._extract_session_id(record) != session_id:
                continue
            event = self._extract_event(record)
            if event not in {"async_success", "success"}:
                continue

            turn = self._extract_turn_from_success_record(record)
            if turn is None:
                continue
            matched_turns.append(turn)

        if not matched_turns:
            return []
        return matched_turns[-self._config.summary_recent_turns :]

    def _collect_latest_usage_from_litellm(self, *, session_id: str) -> dict[str, int]:
        log_path = self._litellm_log_path
        if log_path is None:
            return {"total_tokens": 0, "context_limit": 128000}

        latest_usage = {"total_tokens": 0, "context_limit": 128000}
        for record in self._iter_jsonl_tail(
            log_path, max_bytes=self._litellm_tail_bytes
        ):
            if self._extract_session_id(record) != session_id:
                continue
            event = self._extract_event(record)
            if event not in {"async_success", "success"}:
                continue
            usage = self._extract_usage(record)
            if usage is not None:
                latest_usage = usage
        return latest_usage

    def _iter_jsonl_tail(self, path: Path, *, max_bytes: int) -> list[dict[str, Any]]:
        try:
            file_size = path.stat().st_size
        except OSError:
            return []

        start = max(0, file_size - max_bytes)
        try:
            with path.open("rb") as f:
                f.seek(start)
                data = f.read()
        except OSError:
            return []

        lines = data.splitlines()
        if start > 0 and lines:
            # First line may be a partial line from the middle of the file.
            lines = lines[1:]

        records: list[dict[str, Any]] = []
        for raw in lines:
            if not raw:
                continue
            try:
                line = raw.decode("utf-8", errors="ignore").strip()
            except Exception:
                continue
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                records.append(obj)
        return records

    def _extract_event(self, record: dict[str, Any]) -> str:
        event = record.get("event")
        if isinstance(event, str) and event:
            return event
        kwargs = record.get("kwargs")
        if isinstance(kwargs, dict):
            log_event = kwargs.get("log_event_type")
            if isinstance(log_event, str):
                return log_event
        return "unknown"

    def _extract_session_id(self, record: dict[str, Any]) -> str | None:
        kwargs = record.get("kwargs")
        if not isinstance(kwargs, dict):
            return None

        candidates: list[str] = []

        user = kwargs.get("user")
        if isinstance(user, str):
            candidates.append(user)

        metadata = kwargs.get("metadata")
        if isinstance(metadata, dict):
            for key in ("session_id", "user_id"):
                value = metadata.get(key)
                if isinstance(value, str):
                    candidates.append(value)

        additional_args = kwargs.get("additional_args")
        if isinstance(additional_args, dict):
            headers = additional_args.get("headers")
            if isinstance(headers, dict):
                for key in ("x-claude-code-session-id", "X-Claude-Code-Session-Id"):
                    value = headers.get(key)
                    if isinstance(value, str):
                        candidates.append(value)

        slog = kwargs.get("standard_logging_object")
        if isinstance(slog, dict):
            end_user = slog.get("end_user")
            if isinstance(end_user, str):
                candidates.append(end_user)

        for candidate in candidates:
            sid = self._parse_session_id(candidate)
            if sid:
                return sid
        return None

    def _parse_session_id(self, candidate: str) -> str | None:
        if not candidate:
            return None

        match = _SESSION_IN_USER_RE.search(candidate)
        if match:
            return str(match.group(1))

        if _UUID_RE.fullmatch(candidate):
            return candidate

        match = _UUID_RE.search(candidate)
        if match:
            return str(match.group(1))
        return None

    def _extract_turn_from_success_record(
        self, record: dict[str, Any]
    ) -> dict[str, str] | None:
        kwargs = record.get("kwargs")
        if not isinstance(kwargs, dict):
            return None
        slog = kwargs.get("standard_logging_object")
        if not isinstance(slog, dict):
            return None

        messages = slog.get("messages")
        response = slog.get("response")
        user_message = self._extract_last_user_message(messages)
        agent_message = self._extract_assistant_message(response)
        if not user_message and not agent_message:
            return None

        return {
            "user_message": self._truncate(user_message, self._max_chars_per_message),
            "agent_message": self._truncate(agent_message, self._max_chars_per_message),
        }

    def _extract_usage(self, record: dict[str, Any]) -> dict[str, int] | None:
        kwargs = record.get("kwargs")
        if not isinstance(kwargs, dict):
            return None
        slog = kwargs.get("standard_logging_object")
        if not isinstance(slog, dict):
            return None
        response = slog.get("response")
        if not isinstance(response, dict):
            return None

        usage = response.get("usage")
        if not isinstance(usage, dict):
            return None

        total_tokens = usage.get("total_tokens", 0)
        try:
            total_tokens = int(total_tokens or 0)
        except (TypeError, ValueError):
            total_tokens = 0

        context_limit = usage.get("max_tokens") or usage.get("context_limit") or 128000
        try:
            context_limit = int(context_limit or 128000)
        except (TypeError, ValueError):
            context_limit = 128000

        return {
            "total_tokens": total_tokens,
            "context_limit": context_limit,
        }

    def _extract_last_user_message(self, messages: Any) -> str:
        if not isinstance(messages, list):
            return ""
        for msg in reversed(messages):
            if not isinstance(msg, dict):
                continue
            if msg.get("role") != "user":
                continue
            return self._extract_text(msg.get("content"))
        return ""

    def _extract_assistant_message(self, response: Any) -> str:
        if not isinstance(response, dict):
            return ""
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        choice = choices[0]
        if not isinstance(choice, dict):
            return ""
        message = choice.get("message")
        if not isinstance(message, dict):
            return ""
        return self._extract_text(message.get("content"))

    def _extract_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if not isinstance(item, dict):
                    continue
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                    continue
                nested = item.get("content")
                if isinstance(nested, str):
                    parts.append(nested)
            return "\n".join(p for p in parts if p).strip()

        if isinstance(content, dict):
            for key in ("text", "content"):
                value = content.get(key)
                if isinstance(value, str):
                    return value
        return ""

    def _format_recent_turns(self, turns: list[dict[str, str]]) -> str:
        if not turns:
            log_path = str(self._litellm_log_path) if self._litellm_log_path else "N/A"
            return (
                "<none>\n"
                f"(No matching turns found in LiteLLM log source: {log_path}. "
                "Continue from the latest unfinished user request.)"
            )

        lines: list[str] = []
        for idx, turn in enumerate(turns, start=1):
            user_msg = self._truncate(turn.get("user_message", ""))
            agent_msg = self._truncate(turn.get("agent_message", ""))
            lines.append(f"Turn {idx} User: {user_msg}")
            lines.append(f"Turn {idx} Agent: {agent_msg}")
        return "\n".join(lines)

    @staticmethod
    def _truncate(content: str, max_chars: int = 1500) -> str:
        if len(content) <= max_chars:
            return content
        half = max_chars // 2
        return content[:half] + "\n...[truncated]...\n" + content[-half:]

    @staticmethod
    def _format_proactive_action(action: dict[str, Any]) -> str:
        tool = action.get("tool", "manage_context")
        method = action.get("method")
        value = action.get("value")
        ranges = action.get("ranges")
        if tool == "smart_context_truncate" and isinstance(ranges, list):
            return f"{tool} with ranges={ranges}"
        if method is not None:
            return f"{tool} with method={method}, value={value}"
        return str(tool)
