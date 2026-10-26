"""ClaudeCodeUsersim 的可恢复终态自动重试单测。

网关误拒答 / upstream 断流多发时，run_interactive_turn
对拒答/断流终态自动重试该轮（同 session resume、同一用户消息重发，最多 2
次），仍不行才落"部分输出交还 simulator"兜底；max_turns 不重试。
"""

from types import SimpleNamespace

import pytest

from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.agents.installed.claude_code_usersim import ClaudeCodeUsersim

REFUSAL_LOG = (
    '{"type":"assistant","message":{"content":[{"type":"text","text":"partial work"}]}}\n'
    '{"type":"result","subtype":"error","terminal_reason":"refusal",'
    '"stop_reason":"refusal","result":"API Error: Opus 4.8 can\'t help"}\n'
)
API_ERROR_LOG = (
    '{"type":"result","terminal_reason":"api_error",'
    '"result":"API Error: Upstream stream failed. Please retry."}\n'
)
MAX_TURNS_LOG = '{"type":"result","terminal_reason":"max_turns"}\n'
PLAIN_FAIL_LOG = '{"type":"result","subtype":"error","result":"boom"}\n'


def _make_agent(monkeypatch, tmp_path, exec_side_effects, log_text):
    agent = ClaudeCodeUsersim(logs_dir=tmp_path, model_name="test-model")
    calls: list[dict] = []

    async def fake_prepare(environment, instruction, *, log_path, session_id, resume):
        calls.append({"log_path": log_path, "resume": resume})
        return f"echo stub > {log_path}"

    monkeypatch.setattr(agent, "_prepare_run_command", fake_prepare)

    effects = list(exec_side_effects)

    async def fake_exec(environment, command, env=None):
        if not effects:
            raise AssertionError("exec_as_agent 调用次数超出预期")
        effect = effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect

    monkeypatch.setattr(agent, "exec_as_agent", fake_exec)

    async def fake_read_log(environment, log_path, exc):
        return log_text

    monkeypatch.setattr(agent, "_read_turn_log", fake_read_log)
    return agent, calls


@pytest.mark.asyncio
async def test_refusal_retried_then_succeeds(monkeypatch, tmp_path):
    """拒答×2 后第 3 次成功：返回成功输出，exec 调 3 次，重试用独立日志路径。"""
    exc = NonZeroAgentExitCodeError("cmd failed (exit 1)")
    agent, calls = _make_agent(
        monkeypatch,
        tmp_path,
        [exc, exc, SimpleNamespace(return_code=0, stdout="final answer", stderr="")],
        REFUSAL_LOG,
    )
    out = await agent.run_interactive_turn(
        user_message="继续", environment=None, context=None, session_id="s", turn_idx=1
    )
    assert out == "final answer"
    assert [c["log_path"] for c in calls] == [
        "/logs/agent/claude-code-turn-2.txt",
        "/logs/agent/claude-code-turn-2-retry1.txt",
        "/logs/agent/claude-code-turn-2-retry2.txt",
    ]
    assert all(c["resume"] for c in calls)
    command_dir = tmp_path / "command-2"
    assert (command_dir / "return-code.txt").read_text() == "0"


@pytest.mark.asyncio
async def test_api_error_retried_then_succeeds(monkeypatch, tmp_path):
    """断流×1 后第 2 次成功（api_error 纳入可重试终态）。"""
    exc = NonZeroAgentExitCodeError("cmd failed (exit 1)")
    agent, calls = _make_agent(
        monkeypatch,
        tmp_path,
        [exc, SimpleNamespace(return_code=0, stdout="ok", stderr="")],
        API_ERROR_LOG,
    )
    out = await agent.run_interactive_turn(
        user_message="继续", environment=None, context=None, session_id="s", turn_idx=1
    )
    assert out == "ok"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_refusal_exhausted_falls_back_to_partial(monkeypatch, tmp_path):
    """拒答×3（首发+2 重试耗尽）→ 部分输出兜底，header 标注重试次数。"""
    exc = NonZeroAgentExitCodeError("cmd failed (exit 1)")
    agent, calls = _make_agent(monkeypatch, tmp_path, [exc, exc, exc], REFUSAL_LOG)
    out = await agent.run_interactive_turn(
        user_message="继续", environment=None, context=None, session_id="s", turn_idx=1
    )
    assert "[agent 本轮提前终止: API 拒答" in out
    assert "已自动重试 2 次" in out
    assert "partial work" in out
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_max_turns_not_retried(monkeypatch, tmp_path):
    """max_turns 终态不重试（预算耗尽重试无意义）：一次失败直接兜底。"""
    exc = NonZeroAgentExitCodeError("cmd failed (exit 1)")
    agent, calls = _make_agent(monkeypatch, tmp_path, [exc], MAX_TURNS_LOG)
    out = await agent.run_interactive_turn(
        user_message="继续", environment=None, context=None, session_id="s", turn_idx=1
    )
    assert "[agent 本轮提前终止" in out
    assert "已自动重试" not in out
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_non_recoverable_raises(monkeypatch, tmp_path):
    """未命中可恢复终态的非零退出：原样抛穿。"""
    exc = NonZeroAgentExitCodeError("cmd failed (exit 1)")
    agent, calls = _make_agent(monkeypatch, tmp_path, [exc], PLAIN_FAIL_LOG)
    with pytest.raises(NonZeroAgentExitCodeError):
        await agent.run_interactive_turn(
            user_message="继续",
            environment=None,
            context=None,
            session_id="s",
            turn_idx=1,
        )
    assert len(calls) == 1
