"""Unit tests for Terminus2Usersim (terminus-2 user-simulation agent).

Covers:
- TerminusJSONPlainParser's optional ``user_message`` field;
- Terminus2Usersim.run_interactive_turn state machine with a mocked LLM:
  user_message frames end the turn (same-frame commands execute first),
  task_complete two-step confirmation ends the turn with a summary,
  the per-turn episode cap returns a recoverable-terminal style fallback,
  and the Chat persists across user turns.
"""

import json
from unittest.mock import AsyncMock

import pytest

from harbor.agents.terminus_2.terminus_2_usersim import Terminus2Usersim
from harbor.agents.terminus_2.terminus_xml_plain_parser import TerminusXMLPlainParser
from harbor.agents.terminus_2.terminus_json_plain_parser import (
    TerminusJSONPlainParser,
)
from harbor.agents.terminus_2.tmux_session import TerminalInteractionState
from harbor.llms.base import LLMResponse
from harbor.models.agent.context import AgentContext


def _frame(
    commands: list[dict] | None = None,
    task_complete: bool = False,
    user_message: str | None = None,
    analysis: str = "a",
    plan: str = "p",
) -> str:
    payload: dict = {
        "analysis": analysis,
        "plan": plan,
        "commands": commands or [],
    }
    if task_complete:
        payload["task_complete"] = True
    if user_message is not None:
        payload["user_message"] = user_message
    return json.dumps(payload)


class TestParserUserMessage:
    def setup_method(self):
        self.parser = TerminusJSONPlainParser()

    def test_user_message_parsed(self):
        result = self.parser.parse_response(_frame(user_message="hello user"))
        assert result.error == ""
        assert result.user_message == "hello user"

    def test_user_message_defaults_empty(self):
        result = self.parser.parse_response(_frame())
        assert result.error == ""
        assert result.user_message == ""

    def test_user_message_with_commands_same_frame(self):
        result = self.parser.parse_response(
            _frame(
                commands=[{"keystrokes": "ls\n", "duration": 0.1}],
                user_message="ran ls",
            )
        )
        assert result.error == ""
        assert result.user_message == "ran ls"
        assert [c.keystrokes for c in result.commands] == ["ls\n"]

    def test_user_message_non_string_warns_and_degrades(self):
        response = json.dumps(
            {
                "analysis": "a",
                "plan": "p",
                "commands": [],
                "user_message": 123,
            }
        )
        result = self.parser.parse_response(response)
        assert result.error == ""
        assert result.user_message == ""
        assert "user_message" in result.warning

    def test_user_message_kept_when_task_complete(self):
        result = self.parser.parse_response(
            _frame(task_complete=True, user_message="final answer")
        )
        assert result.error == ""
        assert result.is_task_complete is True
        assert result.user_message == "final answer"


class FakeLLM:
    def __init__(self, contents: list[str]):
        self._contents = list(contents)
        self.calls = []

    def get_model_context_limit(self) -> int:
        return 10**9

    def get_model_output_limit(self) -> int:
        return 8192

    async def call(self, prompt, message_history=None, **kwargs):
        self.calls.append(
            {"prompt": prompt, "message_history": list(message_history or [])}
        )
        if not self._contents:
            raise AssertionError("FakeLLM ran out of queued responses")
        return LLMResponse(content=self._contents.pop(0))


class FakeSession:
    def __init__(self, output: str = "root@app:/app# "):
        self.output = output
        self.send_keys = AsyncMock()
        self.get_incremental_output = AsyncMock(return_value=output)
        self.is_session_alive = AsyncMock(return_value=True)
        self.get_interaction_state = AsyncMock(
            return_value=TerminalInteractionState(
                shell_ready=True, current_command="bash"
            )
        )


def _agent(tmp_path, responses: list[str], **kwargs) -> Terminus2Usersim:
    kwargs.setdefault("enable_summarize", False)
    kwargs.setdefault("suppress_max_turns_warning", True)
    parser_name = kwargs.pop("parser_name", "json")
    agent = Terminus2Usersim(
        logs_dir=tmp_path,
        model_name="fake/model",
        parser_name=parser_name,
        **kwargs,
    )
    agent._llm = FakeLLM(responses)
    agent._session = FakeSession()
    return agent


def _ask_user_calls(agent: Terminus2Usersim):
    return [
        tool_call
        for step in agent._trajectory_steps
        for tool_call in (step.tool_calls or [])
        if tool_call.function_name == "ask_user"
    ]


class TestInit:
    def test_name_and_template(self, tmp_path):
        agent = _agent(tmp_path, [])
        assert agent.name() == "terminus-2-usersim"
        assert "Interacting with the user" in agent._prompt_template

    def test_usersim_max_episodes_kwarg(self, tmp_path):
        agent = _agent(tmp_path, [], usersim_max_episodes_per_turn=7)
        assert agent._usersim_max_episodes_per_turn == 7

    def test_default_usersim_max_episodes(self, tmp_path):
        agent = _agent(tmp_path, [])
        assert agent._usersim_max_episodes_per_turn == 150

    def test_xml_parser_uses_usersim_template(self, tmp_path):
        agent = _agent(tmp_path, [], parser_name="xml")
        assert "<user_message>" in agent._prompt_template

    def test_xml_parser_reads_user_message(self):
        result = TerminusXMLPlainParser().parse_response(
            "<response><analysis>a</analysis><plan>p</plan>"
            "<commands></commands><task_complete>false</task_complete>"
            "<user_message>Which file?</user_message></response>"
        )
        assert result.error == ""
        assert result.user_message == "Which file?"


class TestRunInteractiveTurn:
    @pytest.mark.asyncio
    async def test_session_none_raises(self, tmp_path):
        agent = _agent(tmp_path, [])
        agent._session = None
        with pytest.raises(RuntimeError, match="setup"):
            await agent.run_interactive_turn("hi", None, AgentContext(), "s", 0)

    @pytest.mark.asyncio
    async def test_user_message_ends_turn_after_commands(self, tmp_path):
        agent = _agent(
            tmp_path,
            [
                _frame(
                    commands=[{"keystrokes": "echo hi\n", "duration": 0.1}],
                    analysis="a1",
                    plan="p1",
                ),
                _frame(
                    commands=[{"keystrokes": "ls\n", "duration": 0.1}],
                    user_message="Which file should I edit?",
                    analysis="a2",
                    plan="p2",
                ),
            ],
        )
        context = AgentContext()

        reply = await agent.run_interactive_turn(
            "build the project", None, context, "s0", 0
        )

        assert reply == "Which file should I edit?"
        # 同帧 commands 先执行完才把文本交给用户
        sent = [c.args[0] for c in agent._session.send_keys.await_args_list]
        assert sent == ["echo hi\n", "ls\n"]
        # 轨迹里 user_message 帧记了 ask_user ToolCall
        ask_calls = _ask_user_calls(agent)
        assert len(ask_calls) == 1
        assert ask_calls[0].arguments == {"message": "Which file should I edit?"}
        # 对齐 claude 布局的 per-turn stdout
        stdout = tmp_path / "command-1" / "stdout.txt"
        assert stdout.read_text() == "Which file should I edit?"
        # 首个 prompt 用 usersim 模板渲染，任务文本进入 prompt
        first_prompt = agent._llm.calls[0]["prompt"]
        assert "Interacting with the user" in first_prompt
        assert "build the project" in first_prompt
        # 轨迹已落盘
        assert (tmp_path / "trajectory.json").is_file()

    @pytest.mark.asyncio
    async def test_task_complete_two_step_confirmation_returns_user_message(
        self, tmp_path
    ):
        agent = _agent(
            tmp_path,
            [
                _frame(task_complete=True, analysis="done?", plan="confirm"),
                _frame(
                    task_complete=True,
                    user_message="Final result: everything works.",
                    analysis="all done",
                    plan="deliver",
                ),
            ],
        )
        reply = await agent.run_interactive_turn(
            "fix it", None, AgentContext(), "s0", 0
        )

        assert reply == "Final result: everything works."
        # 第一步确认问句回喂给了 LLM（两步确认语义不变）
        assert "Are you sure" in agent._llm.calls[1]["prompt"]
        complete_calls = [
            tc
            for step in agent._trajectory_steps
            for tc in (step.tool_calls or [])
            if tc.function_name == "mark_task_complete"
        ]
        assert len(complete_calls) == 2

    @pytest.mark.asyncio
    async def test_task_complete_confirmation_falls_back_to_summary(self, tmp_path):
        agent = _agent(
            tmp_path,
            [
                _frame(task_complete=True),
                _frame(task_complete=True, analysis="all done", plan="nothing left"),
            ],
        )
        reply = await agent.run_interactive_turn(
            "fix it", None, AgentContext(), "s0", 0
        )
        assert reply == "Analysis: all done\nPlan: nothing left"

    @pytest.mark.asyncio
    async def test_per_turn_episode_cap_returns_fallback(self, tmp_path):
        agent = _agent(
            tmp_path,
            [
                _frame(
                    commands=[{"keystrokes": "true\n", "duration": 0.1}],
                    analysis=f"a{i}",
                    plan=f"p{i}",
                )
                for i in range(5)
            ],
            usersim_max_episodes_per_turn=2,
        )
        reply = await agent.run_interactive_turn(
            "loop forever", None, AgentContext(), "s0", 0
        )

        assert reply.startswith("[agent 本轮提前终止: 本轮达到内部步数上限")
        assert "usersim_max_episodes_per_turn=2" in reply
        assert "Analysis: a1" in reply  # 最后 assistant 文本
        assert len(agent._llm.calls) == 2  # 只消耗了 per-turn 上限个 episode

    @pytest.mark.asyncio
    async def test_chat_persists_across_turns(self, tmp_path):
        agent = _agent(
            tmp_path,
            [
                _frame(user_message="What is the target file?"),
                _frame(user_message="Done, thanks."),
            ],
        )
        context = AgentContext()

        reply0 = await agent.run_interactive_turn(
            "update the config", None, context, "s0", 0
        )
        assert reply0 == "What is the target file?"
        assert len(agent._chat.messages) == 2  # prompt0 + assistant0

        reply1 = await agent.run_interactive_turn(
            "the target is config.yaml", None, context, "s0", 1
        )
        assert reply1 == "Done, thanks."
        # chat 跨轮持久：第二轮后 4 条消息
        assert len(agent._chat.messages) == 4
        # 第二轮 prompt 携带用户回复与终端状态
        second_prompt = agent._llm.calls[1]["prompt"]
        assert "The user replied:" in second_prompt
        assert "the target is config.yaml" in second_prompt
        assert "Current terminal state:" in second_prompt
        # 两轮的 per-turn stdout 都落盘
        assert (tmp_path / "command-1" / "stdout.txt").read_text() == (
            "What is the target file?"
        )
        assert (tmp_path / "command-2" / "stdout.txt").read_text() == ("Done, thanks.")
        # 全局 episode 计数跨轮累计
        assert agent._n_episodes == 2
