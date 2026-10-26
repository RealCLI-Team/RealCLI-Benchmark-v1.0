"""Terminus 2 agent variant for clibench user-simulation evaluation.

Subclass of Terminus2 wired into harbor's user-simulation interaction loop
(``trial.py:_run_with_user_simulation``). terminus-2 is a harbor-driven
turn-taking loop (LLM returns JSON -> harbor executes the commands -> terminal
output is fed back), so every agent reply already passes through harbor and no
AUQ hook / RPC side channel is needed: asking the user = adding an optional
``user_message`` field to the reply JSON. A reply with a non-empty
``user_message`` ends the current user turn and the text is handed to the user
simulator; the simulator's reply arrives as the next turn's user message.

Differences from the base Terminus2 ``run()`` loop:

- The Chat persists across user turns (``_reset_per_run_state()`` is NOT
  called — it would clear the chat; context compaction strategies keep
  working inside the per-turn episode loop).
- ``user_message`` may share a frame with ``commands``: commands are executed
  first, then the text ends the turn.
- ``task_complete`` keeps its two-step confirmation semantics, but a confirmed
  completion only ends the current user turn — it does not terminate the
  trial (termination is controlled by the simulator script / max_turns).
- Hitting ``usersim_max_episodes_per_turn`` returns the last assistant text
  with a recoverable-terminal header (mirroring ClaudeCodeUsersim) instead of
  raising through the interaction loop; the conversation resumes next turn.
- Each turn's reply is written to ``logs_dir/command-{turn_idx+1}/stdout.txt``
  to match the claude_code_usersim log layout.

Use via agent import_path:
    harbor.agents.terminus_2.terminus_2_usersim:Terminus2Usersim
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import override

from harbor.agents.terminus_2.terminus_2 import (
    Terminus2,
    _terminal_observation_source_call_id,
)
from harbor.environments.base import BaseEnvironment
from harbor.llms.chat import Chat
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import (
    Metrics,
    Observation,
    ObservationResult,
    Step,
    ToolCall,
)


class Terminus2Usersim(Terminus2):
    """Terminus2 with a run_interactive_turn() user-simulation loop."""

    def __init__(self, *args, usersim_max_episodes_per_turn: int = 150, **kwargs):
        self._usersim_max_episodes_per_turn = usersim_max_episodes_per_turn
        self._usersim_original_instruction = ""
        super().__init__(*args, **kwargs)

    @staticmethod
    @override
    def name() -> str:
        return "terminus-2-usersim"

    @override
    def _get_prompt_template_path(self) -> Path:
        if self.options.parser_name == "json":
            return (
                Path(__file__).parent / "templates" / "terminus-json-plain-usersim.txt"
            )
        if self.options.parser_name == "xml":
            return (
                Path(__file__).parent / "templates" / "terminus-xml-plain-usersim.txt"
            )
        raise ValueError(
            f"Unknown parser_name: {self.options.parser_name}. Use 'json' or 'xml'."
        )

    @override
    async def run_interactive_turn(
        self,
        user_message: str,
        environment: BaseEnvironment,
        context: AgentContext,
        session_id: str,
        turn_idx: int,
    ) -> str:
        if self._session is None:
            raise RuntimeError(
                "Terminus2Usersim has no tmux session: agent.setup(environment) "
                "must complete before run_interactive_turn() (trial.py calls "
                "setup() before the interaction loop)."
            )
        session = self._session
        self._context = context

        if turn_idx == 0 or self._chat is None:
            # chat 跨用户轮持久：只在首轮（或 trial context reset 后重新从
            # turn_idx=0 开始）重建 Chat。
            self._chat = Chat(
                self._llm, interleaved_thinking=self.options.interleaved_thinking
            )
            self._usersim_original_instruction = user_message
        chat = self._chat

        terminal_state = self._limit_output_length(
            await session.get_incremental_output()
        )
        if turn_idx == 0:
            prompt = self._prompt_template.format(
                instruction=user_message,
                terminal_state=terminal_state,
            )
        else:
            prompt = (
                f"The user replied:\n{user_message}\n\n"
                f"Current terminal state:\n{terminal_state}"
            )
        self._trajectory_steps.append(
            Step(
                step_id=len(self._trajectory_steps) + 1,
                timestamp=datetime.now(timezone.utc).isoformat(),
                source="user",
                message=prompt,
            )
        )

        original_instruction = self._usersim_original_instruction
        last_agent_text = ""
        episodes_this_turn = 0
        turn_reply: str | None = None

        while episodes_this_turn < self._usersim_max_episodes_per_turn:
            if self._n_episodes >= self._max_episodes:
                break
            if not await session.is_session_alive():
                self.logger.debug(
                    "Session has ended, breaking out of usersim turn loop"
                )
                break

            episode = self._n_episodes
            self._n_episodes += 1
            episodes_this_turn += 1

            if original_instruction and self.options.enable_summarize:
                proactive_summary_result = await self._check_proactive_summarization(
                    chat,
                    original_instruction,
                    session,
                )
                if proactive_summary_result:
                    prompt, subagent_refs = proactive_summary_result
                    self._pending_subagent_refs = subagent_refs
                    self._pending_handoff_prompt = prompt

            tokens_before_input = chat.total_input_tokens
            tokens_before_output = chat.total_output_tokens
            tokens_before_cache = chat.total_cache_tokens
            cost_before = chat.total_cost

            (
                commands,
                is_task_complete,
                feedback,
                analysis,
                plan,
                llm_response,
            ) = await self._handle_llm_interaction(
                chat, prompt, original_instruction, session
            )

            # user_message 协议字段不由 _handle_llm_interaction 返回，用同一
            # parser 复解析一次取出（parse 是确定性的，开销可忽略）。
            parsed = self._parser.parse_response(llm_response.content)
            user_message_out = getattr(parsed, "user_message", "")
            if not isinstance(user_message_out, str):
                user_message_out = ""

            if self._pending_subagent_refs:
                self._trajectory_steps.append(
                    Step(
                        step_id=len(self._trajectory_steps) + 1,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        source="system",
                        message="Performed context summarization and handoff to continue task.",
                        observation=Observation(
                            results=[
                                ObservationResult(
                                    subagent_trajectory_ref=self._pending_subagent_refs
                                )
                            ]
                        ),
                        extra={
                            "context_management": {
                                "type": "compaction",
                                "boundary": "replace",
                            }
                        },
                    )
                )
                self._pending_subagent_refs = None

            if self._pending_handoff_prompt:
                if (self.options.trajectory_config or {}).get("linear_history", False):
                    self._split_trajectory_on_summarization(
                        self._pending_handoff_prompt
                    )
                else:
                    self._trajectory_steps.append(
                        Step(
                            step_id=len(self._trajectory_steps) + 1,
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            source="user",
                            message=self._pending_handoff_prompt,
                        )
                    )
                self._pending_handoff_prompt = None

            if (self.options.trajectory_config or {}).get("raw_content", False):
                message_content = llm_response.content
            else:
                message_parts = []
                if analysis:
                    message_parts.append(f"Analysis: {analysis}")
                if plan:
                    message_parts.append(f"Plan: {plan}")
                message_content = "\n".join(message_parts) if message_parts else ""

            context.n_input_tokens = chat.total_input_tokens
            context.n_output_tokens = chat.total_output_tokens
            context.n_cache_tokens = chat.total_cache_tokens
            context.cost_usd = chat.total_cost if chat.total_cost > 0 else None

            if feedback and "ERROR:" in feedback:
                prompt = (
                    f"Previous response had parsing errors:\n{feedback}\n\n"
                    f"Please fix these issues and provide a proper "
                    f"{self._get_error_response_type()}."
                )
                cache_tokens_used = chat.total_cache_tokens - tokens_before_cache
                step_cost = chat.total_cost - cost_before
                if llm_response.model_name:
                    self._last_response_model_name = llm_response.model_name
                self._trajectory_steps.append(
                    Step(
                        step_id=len(self._trajectory_steps) + 1,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        source="agent",
                        model_name=llm_response.model_name or self._model_name,
                        message=llm_response.content,
                        reasoning_content=llm_response.reasoning_content,
                        observation=Observation(
                            results=[
                                ObservationResult(
                                    content=prompt,
                                )
                            ]
                        ),
                        metrics=Metrics(
                            prompt_tokens=chat.total_input_tokens - tokens_before_input,
                            completion_tokens=chat.total_output_tokens
                            - tokens_before_output,
                            cached_tokens=cache_tokens_used
                            if cache_tokens_used > 0
                            else None,
                            cost_usd=step_cost if step_cost > 0 else None,
                            prompt_token_ids=llm_response.prompt_token_ids,
                            completion_token_ids=llm_response.completion_token_ids,
                            logprobs=llm_response.logprobs,
                        ),
                    )
                )
                continue

            _, terminal_output = await self._execute_commands(commands, session)

            was_pending_completion = self._pending_completion

            if is_task_complete:
                if self._pending_completion:
                    observation = terminal_output
                else:
                    self._pending_completion = True
                    observation = self._get_completion_confirmation_message(
                        terminal_output
                    )
            else:
                self._pending_completion = False
                if feedback and "WARNINGS:" in feedback:
                    observation = (
                        f"Previous response had warnings:\n{feedback}\n\n"
                        f"{self._limit_output_length(terminal_output)}"
                    )
                else:
                    observation = self._limit_output_length(terminal_output)

            cache_tokens_used = chat.total_cache_tokens - tokens_before_cache
            step_cost = chat.total_cost - cost_before

            tool_calls: list[ToolCall] | None = None
            observation_results: list[ObservationResult] = []

            if not (self.options.trajectory_config or {}).get("raw_content", False):
                tool_calls_list: list[ToolCall] = []

                if commands:
                    for i, cmd in enumerate(commands):
                        tool_calls_list.append(
                            ToolCall(
                                tool_call_id=f"call_{episode}_{i + 1}",
                                function_name="bash_command",
                                arguments={
                                    "keystrokes": cmd.keystrokes,
                                    "duration": cmd.duration_sec,
                                },
                            )
                        )
                    observation_results.append(
                        ObservationResult(
                            source_call_id=_terminal_observation_source_call_id(
                                commands, episode
                            ),
                            content=observation,
                        )
                    )

                if is_task_complete:
                    tool_calls_list.append(
                        ToolCall(
                            tool_call_id=f"call_{episode}_task_complete",
                            function_name="mark_task_complete",
                            arguments={},
                        )
                    )
                    if not commands:
                        observation_results.append(
                            ObservationResult(
                                content=observation,
                            )
                        )
                elif not commands:
                    observation_results.append(
                        ObservationResult(
                            content=observation,
                        )
                    )

                if user_message_out:
                    tool_calls_list.append(
                        ToolCall(
                            tool_call_id=f"call_{episode}_ask_user",
                            function_name="ask_user",
                            arguments={"message": user_message_out},
                        )
                    )

                tool_calls = tool_calls_list or None
            else:
                observation_results.append(
                    ObservationResult(
                        content=observation,
                    )
                )

            if llm_response.model_name:
                self._last_response_model_name = llm_response.model_name
            self._trajectory_steps.append(
                Step(
                    step_id=len(self._trajectory_steps) + 1,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    source="agent",
                    model_name=llm_response.model_name or self._model_name,
                    message=message_content,
                    reasoning_content=llm_response.reasoning_content,
                    tool_calls=tool_calls,
                    observation=Observation(results=observation_results),
                    metrics=Metrics(
                        prompt_tokens=chat.total_input_tokens - tokens_before_input,
                        completion_tokens=chat.total_output_tokens
                        - tokens_before_output,
                        cached_tokens=cache_tokens_used
                        if cache_tokens_used > 0
                        else None,
                        cost_usd=step_cost if step_cost > 0 else None,
                        prompt_token_ids=llm_response.prompt_token_ids,
                        completion_token_ids=llm_response.completion_token_ids,
                        logprobs=llm_response.logprobs,
                    ),
                )
            )

            self._dump_trajectory()

            last_agent_text = message_content or llm_response.content

            # 轮结束条件 1：task_complete 两步确认走完——只结束本轮，不终止
            # trial（终止由 simulator 剧本 should_stop/max_turns 控制）。
            if is_task_complete and was_pending_completion:
                turn_reply = user_message_out or message_content or llm_response.content
                break

            # 轮结束条件 2：非空 user_message——同帧 commands 已在上面执行
            # 完，把文本交回 trial 喂 simulator。
            if user_message_out:
                turn_reply = user_message_out
                break

            prompt = observation

        if turn_reply is None:
            # 轮结束条件 3：打满 per-turn 上限 / 全局 max_turns / session
            # 死亡——仿 ClaudeCodeUsersim 可恢复终态：部分输出交还
            # simulator，会话保留，下一轮继续。
            if episodes_this_turn >= self._usersim_max_episodes_per_turn:
                reason = (
                    "本轮达到内部步数上限 "
                    f"usersim_max_episodes_per_turn={self._usersim_max_episodes_per_turn}"
                )
            elif self._n_episodes >= self._max_episodes:
                reason = f"达到全局 max_turns={self._max_episodes} 上限"
            else:
                reason = "tmux session 已结束"
            header = f"[agent 本轮提前终止: {reason}]"
            turn_reply = f"{header}\n\n{last_agent_text}" if last_agent_text else header

        # 对齐 claude 布局：每轮返回文本落 command-{turn}/stdout.txt，
        # 下游按 turn 读日志的工具零适配。
        command_dir = self.logs_dir / f"command-{turn_idx + 1}"
        command_dir.mkdir(parents=True, exist_ok=True)
        (command_dir / "stdout.txt").write_text(turn_reply)

        # token/cost 记账（对齐 run() 的 finally 段，含 subagent 指标）。
        context.rollout_details = chat.rollout_details + self._subagent_rollout_details
        context.n_input_tokens = (
            chat.total_input_tokens + self._subagent_metrics.total_prompt_tokens
        )
        context.n_output_tokens = (
            chat.total_output_tokens + self._subagent_metrics.total_completion_tokens
        )
        context.n_cache_tokens = (
            chat.total_cache_tokens + self._subagent_metrics.total_cached_tokens
        )
        total_cost = chat.total_cost + self._subagent_metrics.total_cost_usd
        context.cost_usd = total_cost if total_cost > 0 else None
        context.metadata = {
            "n_episodes": self._n_episodes,
            "api_request_times_msec": self._api_request_times,
            "summarization_count": self._summarization_count,
        }
        if self.options.store_all_messages:
            context.metadata["all_messages"] = chat.messages

        self._dump_trajectory()
        return turn_reply
