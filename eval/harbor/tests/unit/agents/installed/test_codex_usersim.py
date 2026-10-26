"""Unit tests for CodexUsersim (codex user-simulation agent).

Covers:
- run_interactive_turn command construction: turn 0 is a fresh `codex exec`
  (no resume, no sessions restore), turn N restores sessions from
  /logs/agent/sessions and runs `codex exec resume --last`;
- _extract_last_agent_text on codex 0.146 `--json` JSONL event streams
  (normal extraction / empty log / non-JSON lines mixed in);
- recoverable terminal states: a turn whose event stream carries
  turn.failed / error markers returns partial output instead of raising,
  while unrecognized failures re-raise;
- the per-turn command-N/{command,stdout,return-code}.txt log layout.
"""

import json
from unittest.mock import AsyncMock

import pytest

from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.agents.installed.codex_usersim import (
    CodexUsersim,
    _detect_recoverable_terminal_reason,
    _extract_last_agent_text,
)
from harbor.models.agent.context import AgentContext

MODEL = "claude-sonnet-4-6"
BASE_URL = "http://llm-gateway.example:8000/v1"


def _jsonl(events: list[dict]) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


def _agent_message_event(text: str, item_id: str = "item_1") -> dict:
    return {
        "type": "item.completed",
        "item": {"id": item_id, "type": "agent_message", "text": text},
    }


class _Result:
    def __init__(self, return_code=0, stdout="", stderr=""):
        self.return_code = return_code
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = False


def _make_env(exec_side_effect=None) -> AsyncMock:
    mock_env = AsyncMock()
    mock_env.default_user = "agent"
    mock_env.capabilities.mounted = True  # skip the download_dir path
    if exec_side_effect is None:
        mock_env.exec.return_value = _Result()
    else:
        mock_env.exec.side_effect = exec_side_effect
    return mock_env


def _exec_commands(mock_env: AsyncMock) -> list[str]:
    return [
        c.kwargs["command"]
        for c in mock_env.exec.call_args_list
        if "command" in c.kwargs
    ]


def _one_containing(commands: list[str], needle: str) -> str:
    matches = [c for c in commands if needle in c]
    assert len(matches) == 1, (
        f"expected exactly one command containing {needle!r}, got {len(matches)}"
    )
    return matches[0]


def _agent(temp_dir, monkeypatch, **kwargs) -> CodexUsersim:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    kwargs.setdefault("model_name", MODEL)
    kwargs.setdefault("openai_base_url", BASE_URL)
    return CodexUsersim(logs_dir=temp_dir, **kwargs)


class TestName:
    def test_name(self, temp_dir, monkeypatch):
        assert _agent(temp_dir, monkeypatch).name() == "codex-usersim"


class TestCommandConstruction:
    @pytest.mark.asyncio
    async def test_turn0_fresh_exec_no_resume(self, temp_dir, monkeypatch):
        agent = _agent(temp_dir, monkeypatch)
        mock_env = _make_env()

        reply = await agent.run_interactive_turn(
            "build the project", mock_env, AgentContext(), "s0", 0
        )

        commands = _exec_commands(mock_env)
        exec_cmd = _one_containing(commands, "codex exec")
        assert "codex exec resume" not in exec_cmd
        assert "resume --last" not in exec_cmd
        assert f"--model {MODEL} " in exec_cmd
        assert "--json " in exec_cmd
        assert "-- 'build the project'" in exec_cmd
        # turn 0 的 setup 不含 sessions 恢复块
        assert not any("Cannot resume Codex" in c for c in commands)
        # sessions 每轮 cp 回 /logs/agent/sessions + 清理 CODEX_HOME
        assert any(
            'cp -R "$CODEX_HOME/sessions" /logs/agent/sessions' in c for c in commands
        )
        assert any('rm -rf /tmp/codex-secrets "$CODEX_HOME"' in c for c in commands)
        # 事件流取不到 agent message（mock 空输出）→ 退回 tail（空串）
        assert reply == ""

    @pytest.mark.asyncio
    async def test_turn_n_resume_restores_sessions(self, temp_dir, monkeypatch):
        agent = _agent(temp_dir, monkeypatch)
        mock_env = _make_env()

        await agent.run_interactive_turn("turn one", mock_env, AgentContext(), "s0", 0)
        await agent.run_interactive_turn("turn two", mock_env, AgentContext(), "s0", 1)

        commands = _exec_commands(mock_env)
        # turn N 的 setup 含 sessions 拷回块
        setup = _one_containing(commands, "Cannot resume Codex")
        assert 'cp -R /logs/agent/sessions "$CODEX_HOME/sessions"' in setup
        # turn N 的 exec 带 resume --last
        resume_execs = [c for c in commands if "codex exec resume --last" in c]
        assert len(resume_execs) == 1
        assert "-- 'turn two'" in resume_execs[0]
        # 两轮各自只跑一次 codex exec
        assert sum("codex exec" in c and "| tee" in c for c in commands) == 2

    @pytest.mark.asyncio
    async def test_web_search_flag_from_env(self, temp_dir, monkeypatch):
        monkeypatch.setenv("HARBOR_CODEX_WEB_SEARCH", "disabled")
        agent = _agent(temp_dir, monkeypatch)
        mock_env = _make_env()

        await agent.run_interactive_turn("hi", mock_env, AgentContext(), "s0", 0)

        exec_cmd = _one_containing(_exec_commands(mock_env), "codex exec")
        assert "-c 'web_search=\"disabled\"'" in exec_cmd

    @pytest.mark.asyncio
    async def test_missing_model_raises_before_exec(self, temp_dir, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        agent = CodexUsersim(logs_dir=temp_dir, model_name=None)
        mock_env = _make_env()
        with pytest.raises(ValueError, match="Model name is required"):
            await agent.run_interactive_turn("hi", mock_env, AgentContext(), "s0", 0)
        mock_env.exec.assert_not_called()


class TestExtractLastAgentText:
    def test_normal_extraction(self):
        log = _jsonl(
            [
                {"type": "thread.started", "thread_id": "t1"},
                {"type": "turn.started"},
                {
                    "type": "item.completed",
                    "item": {"id": "i1", "type": "reasoning", "text": "thinking"},
                },
                _agent_message_event("first reply", "i2"),
                {
                    "type": "item.completed",
                    "item": {
                        "id": "i3",
                        "type": "command_execution",
                        "command": "ls",
                        "aggregated_output": "file.txt",
                        "exit_code": 0,
                        "status": "completed",
                    },
                },
                _agent_message_event("final reply", "i4"),
                {"type": "turn.completed", "usage": {}},
            ]
        )
        assert _extract_last_agent_text(log) == "final reply"

    def test_empty_log(self):
        assert _extract_last_agent_text("") == ""

    def test_non_json_lines_mixed(self):
        log = (
            "Reading prompt from stdin...\n"
            + _jsonl([_agent_message_event("hello")])
            + "not json at all\n{ broken json\n"
        )
        assert _extract_last_agent_text(log) == "hello"

    def test_no_agent_message(self):
        log = _jsonl(
            [
                {"type": "thread.started", "thread_id": "t1"},
                {
                    "type": "item.completed",
                    "item": {"id": "i1", "type": "reasoning", "text": "thinking"},
                },
            ]
        )
        assert _extract_last_agent_text(log) == ""

    def test_item_type_fallback_naming(self):
        log = _jsonl(
            [
                {
                    "type": "item.completed",
                    "item": {
                        "id": "i1",
                        "item_type": "assistant_message",
                        "text": "legacy naming",
                    },
                }
            ]
        )
        assert _extract_last_agent_text(log) == "legacy naming"


class TestRecoverableTerminal:
    def test_turn_failed_detected(self):
        log = _jsonl(
            [
                {"type": "thread.started", "thread_id": "t1"},
                {"type": "turn.started"},
                {
                    "type": "turn.failed",
                    "error": {"message": "stream error: idle timeout"},
                },
            ]
        )
        reason = _detect_recoverable_terminal_reason(log)
        assert reason is not None
        assert "turn.failed" in reason
        assert "idle timeout" in reason

    def test_error_event_detected(self):
        log = _jsonl(
            [
                {"type": "thread.started", "thread_id": "t1"},
                {"type": "error", "message": "usage limit exceeded"},
            ]
        )
        reason = _detect_recoverable_terminal_reason(log)
        assert reason is not None
        assert "usage limit exceeded" in reason

    def test_no_failure_marker(self):
        log = _jsonl(
            [
                {"type": "thread.started", "thread_id": "t1"},
                _agent_message_event("done"),
                {"type": "turn.completed", "usage": {}},
            ]
        )
        assert _detect_recoverable_terminal_reason(log) is None

    def test_no_codex_events_not_recoverable(self):
        assert _detect_recoverable_terminal_reason("") is None
        assert _detect_recoverable_terminal_reason("codex: command not found\n") is None

    @pytest.mark.asyncio
    async def test_recoverable_failure_returns_partial_output(
        self, temp_dir, monkeypatch
    ):
        agent = _agent(temp_dir, monkeypatch)
        turn_log = _jsonl(
            [
                {"type": "thread.started", "thread_id": "t1"},
                {"type": "turn.started"},
                _agent_message_event("partial answer so far"),
                {
                    "type": "turn.failed",
                    "error": {"message": "stream error: disconnected"},
                },
            ]
        )

        def side_effect(command=None, user=None, env=None, cwd=None, timeout_sec=None):
            if command and "codex exec" in command:
                return _Result(return_code=1, stdout="turn.failed ...", stderr="")
            if command and command.startswith("cat /logs/agent/codex.txt"):
                return _Result(return_code=0, stdout=turn_log)
            return _Result()

        mock_env = _make_env(side_effect)

        reply = await agent.run_interactive_turn(
            "do it", mock_env, AgentContext(), "s0", 0
        )

        assert reply.startswith("[agent 本轮提前终止: codex turn.failed")
        assert "partial answer so far" in reply
        # 现场落盘：return-code 从异常消息解析，stdout 用完整事件流
        command_dir = temp_dir / "command-1"
        assert (command_dir / "return-code.txt").read_text() == "1"
        assert (command_dir / "stdout.txt").read_text() == turn_log
        # sessions persist/cleanup 仍然执行（finally 不被恢复路径跳过）
        commands = _exec_commands(mock_env)
        assert any('cp -R "$CODEX_HOME/sessions"' in c for c in commands)

    @pytest.mark.asyncio
    async def test_unrecoverable_failure_reraises(self, temp_dir, monkeypatch):
        agent = _agent(temp_dir, monkeypatch)

        def side_effect(command=None, user=None, env=None, cwd=None, timeout_sec=None):
            if command and "codex exec" in command:
                return _Result(return_code=127, stdout="", stderr="")
            if command and command.startswith("cat /logs/agent/codex.txt"):
                return _Result(return_code=0, stdout="codex: command not found\n")
            return _Result()

        mock_env = _make_env(side_effect)

        with pytest.raises(NonZeroAgentExitCodeError):
            await agent.run_interactive_turn("do it", mock_env, AgentContext(), "s0", 0)
        # 未命中可恢复终态：不写 return-code.txt（现场不属于恢复布局）
        assert not (temp_dir / "command-1" / "return-code.txt").exists()

    @pytest.mark.asyncio
    async def test_failure_without_events_reraises_even_with_partial_text(
        self, temp_dir, monkeypatch
    ):
        """事件流里只有普通事件、没有 turn.failed/error → 不可恢复。"""
        agent = _agent(temp_dir, monkeypatch)
        turn_log = _jsonl(
            [
                {"type": "thread.started", "thread_id": "t1"},
                _agent_message_event("some text"),
            ]
        )

        def side_effect(command=None, user=None, env=None, cwd=None, timeout_sec=None):
            if command and "codex exec" in command:
                return _Result(return_code=1, stdout="", stderr="")
            if command and command.startswith("cat /logs/agent/codex.txt"):
                return _Result(return_code=0, stdout=turn_log)
            return _Result()

        mock_env = _make_env(side_effect)
        with pytest.raises(NonZeroAgentExitCodeError):
            await agent.run_interactive_turn("do it", mock_env, AgentContext(), "s0", 0)


class TestTurnLogsAndReply:
    @pytest.mark.asyncio
    async def test_command_files_and_reply_extraction(self, temp_dir, monkeypatch):
        agent = _agent(temp_dir, monkeypatch)
        stream = _jsonl(
            [
                {"type": "thread.started", "thread_id": "t1"},
                _agent_message_event("Which file should I edit?"),
                {"type": "turn.completed", "usage": {}},
            ]
        )

        def side_effect(command=None, user=None, env=None, cwd=None, timeout_sec=None):
            if command and "codex exec" in command:
                return _Result(return_code=0, stdout=stream)
            return _Result()

        mock_env = _make_env(side_effect)

        reply = await agent.run_interactive_turn(
            "update the config", mock_env, AgentContext(), "s0", 0
        )

        assert reply == "Which file should I edit?"
        command_dir = temp_dir / "command-1"
        # command.txt 是原始命令；environment.exec 收到的是 base._exec 加了
        # "set -o pipefail; " 前缀的版本
        exec_cmd = _one_containing(_exec_commands(mock_env), "codex exec")
        assert (command_dir / "command.txt").read_text() == exec_cmd.removeprefix(
            "set -o pipefail; "
        )
        assert (command_dir / "return-code.txt").read_text() == "0"
        assert (command_dir / "stdout.txt").read_text() == stream

    @pytest.mark.asyncio
    async def test_reply_falls_back_to_stdout_tail(self, temp_dir, monkeypatch):
        agent = _agent(temp_dir, monkeypatch)
        noisy = "x" * 5000

        def side_effect(command=None, user=None, env=None, cwd=None, timeout_sec=None):
            if command and "codex exec" in command:
                return _Result(return_code=0, stdout=noisy)
            return _Result()

        mock_env = _make_env(side_effect)
        reply = await agent.run_interactive_turn(
            "hi", mock_env, AgentContext(), "s0", 0
        )
        assert len(reply) == 4000
        assert reply == noisy[-4000:]

    @pytest.mark.asyncio
    async def test_second_turn_writes_command_2(self, temp_dir, monkeypatch):
        agent = _agent(temp_dir, monkeypatch)
        mock_env = _make_env()
        await agent.run_interactive_turn("one", mock_env, AgentContext(), "s0", 0)
        await agent.run_interactive_turn("two", mock_env, AgentContext(), "s0", 1)
        assert (temp_dir / "command-1" / "command.txt").is_file()
        assert (temp_dir / "command-2" / "command.txt").is_file()
        assert "resume --last" in (temp_dir / "command-2" / "command.txt").read_text()


class TestTrajectoryRefresh:
    @pytest.mark.asyncio
    async def test_trajectory_written_from_local_sessions(self, temp_dir, monkeypatch):
        """mounted 环境下本地 logs_dir/sessions 已有 rollout 时，每轮结束
        转出 trajectory.json。"""
        agent = _agent(temp_dir, monkeypatch)
        session_dir = temp_dir / "sessions" / "2026" / "09" / "16"
        session_dir.mkdir(parents=True)
        events = [
            {
                "type": "session_meta",
                "payload": {"id": "session-1", "cli_version": "0.146.0"},
            },
            {
                "type": "response_item",
                "timestamp": "2026-09-16T00:00:00Z",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Done."}],
                },
            },
        ]
        (session_dir / "rollout-1.jsonl").write_text(_jsonl(events))

        mock_env = _make_env()
        context = AgentContext()
        await agent.run_interactive_turn("hi", mock_env, context, "s0", 0)

        trajectory_path = temp_dir / "trajectory.json"
        assert trajectory_path.is_file()
        payload = json.loads(trajectory_path.read_text())
        assert payload["schema_version"] == "ATIF-v1.7"
        assert payload["session_id"] == "session-1"

    @pytest.mark.asyncio
    async def test_trajectory_refresh_failure_does_not_break_turn(
        self, temp_dir, monkeypatch
    ):
        """非 mounted 环境 download_dir 抛错 → 降级 debug，轮次正常返回。"""
        agent = _agent(temp_dir, monkeypatch)
        mock_env = _make_env()
        mock_env.capabilities.mounted = False
        mock_env.download_dir.side_effect = RuntimeError("ssh down")

        reply = await agent.run_interactive_turn(
            "hi", mock_env, AgentContext(), "s0", 0
        )
        assert reply == ""
        assert not (temp_dir / "trajectory.json").exists()
