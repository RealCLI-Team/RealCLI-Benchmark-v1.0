from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ContextToolsState:
    """Persists context-tool status and queued actions for a trial."""

    def __init__(self, agent_logs_dir: Path) -> None:
        self._agent_logs_dir = agent_logs_dir
        self._agent_logs_dir.mkdir(parents=True, exist_ok=True)
        self._status_path = self._agent_logs_dir / "context_status.json"
        self._actions_path = self._agent_logs_dir / "context_actions.json"

    @property
    def status_path(self) -> Path:
        return self._status_path

    @property
    def actions_path(self) -> Path:
        return self._actions_path

    def write_status(self, payload: dict[str, Any]) -> None:
        self._write_json(self._status_path, payload)

    def read_status(self) -> dict[str, Any]:
        data = self._read_json(self._status_path)
        return data if isinstance(data, dict) else {}

    def append_action(self, payload: dict[str, Any]) -> None:
        actions = self.read_actions()
        actions.append(payload)
        self._write_json(self._actions_path, {"actions": actions})

    def read_actions(self) -> list[dict[str, Any]]:
        payload = self._read_json(self._actions_path)
        if not isinstance(payload, dict):
            return []
        actions = payload.get("actions")
        if not isinstance(actions, list):
            return []
        return [item for item in actions if isinstance(item, dict)]

    def pop_pending_action(self) -> dict[str, Any] | None:
        actions = self.read_actions()
        if not actions:
            return None
        action = actions.pop(0)
        self._write_json(self._actions_path, {"actions": actions})
        return action

    def clear_actions(self) -> None:
        self._write_json(self._actions_path, {"actions": []})

    def _read_json(self, path: Path) -> Any:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_json(self, path: Path, payload: Any) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(path)
