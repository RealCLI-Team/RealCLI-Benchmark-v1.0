"""Codex agent variant for clibench user-simulation evaluation.

Subclass of ``_LegacyCodex`` (直起 CLI 的 codex 实现) wired into harbor's
user-simulation interaction loop (``trial.py:_run_with_user_simulation``).
codex 是 CLI 型 agent（与 claude-code 同类），交互机制 = 每轮一次
``codex exec`` 调用 + 会话 resume：

- ``turn_idx == 0``：fresh ``codex exec``；
- ``turn_idx > 0``：``codex exec resume --last``（sessions 先从
  ``/logs/agent/sessions`` 拷回 ``$CODEX_HOME/sessions``——codex 0.146 的
  resume 是追加到同一 rollout 文件，见上游 rollout/src/recorder.rs
  ``RolloutRecorderParams::Resume`` → ``open_rollout_for_append``）。

每轮复用 ``_LegacyCodex`` 的 setup/exec/persist/cleanup 拼装
（``_exec_codex_setup`` / ``_build_exec_command`` / ``_persist_sessions`` /
``_cleanup_codex_home``，2026-09-16 从 run() 抽出的不改行为方法，默认
路径已做逐字节对照验证）：cleanup 每轮清掉 ``$CODEX_HOME``，下一轮
setup 幂等重放，config.toml 不存在追加重复键问题。

与基类 run() 的差异（全部是 usersim 必需）：

1. 每轮写 ``logs_dir/command-{turn+1}/{command.txt,stdout.txt,
   return-code.txt}``（对齐 claude/terminus 布局，judge transcript_hint
   零适配）。
2. 回复提取：``_extract_last_agent_text()`` 从容器内
   ``/logs/agent/codex.txt``（codex ``--json`` 的 JSONL 事件流）取最后
   一条 agent message 文本返回；取不到退原始输出 tail。
3. 可恢复终态：仿 ClaudeCodeUsersim——codex exec 非零退出时，若该轮
   事件流里出现 ``turn.failed`` / ``error`` 类事件（API 错误 / 流断开 /
   usage limit），会话 rollout 已增量落盘、未损坏，把该轮部分输出交还
   simulator（下一轮 resume --last 续跑同一会话）；未命中 re-raise。
   marker 先按 codex 0.146 事件格式（exec/src/exec_events.rs）的合理
   集合写，冒烟阶段按实测校准。
4. 每轮结束把 ``/logs/agent/sessions`` 转成 ATIF 写
   ``logs_dir/trajectory.json``（复用 ``_convert_events_to_trajectory``；
   失败降级为 debug 日志不炸轮次），judge 主数据源与 terminus2 一致。

Use via agent import_path:
    harbor.agents.installed.codex_usersim:CodexUsersim
"""

import json
import re
from pathlib import Path
from typing import override

from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.agents.installed.codex_legacy import _LegacyCodex
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.paths import EnvironmentPaths
from harbor.utils.trajectory_utils import format_trajectory_json

# 可恢复终态 marker（2026-09-16，先按 codex 0.146 `codex exec --json`
# 事件格式 exec/src/exec_events.rs 的合理集合）：turn.failed
# （TurnFailedEvent，error.message 带原因）与 error（ThreadErrorEvent，
# 流断开 / usage limit 等致命流错误）。冒烟阶段按实测校准。
# 判定前提：事件流里必须真出现过 codex 事件（证明 CLI 正常启动、
# session 已建立、rollout 已增量落盘）；空输出 / 无事件 = 基础设施类
# 失败（二进制缺失、容器异常等），不可恢复，原样 re-raise。
_RECOVERABLE_EVENT_TYPES = ("turn.failed", "error")

# 回复提取认的 item 文本类型（codex 0.146 ThreadItemDetails serde tag
# 为 "type"、snake_case 命名）；assistant_message 防御早期命名。
_AGENT_MESSAGE_ITEM_TYPES = ("agent_message", "assistant_message")

# 取不到 agent message 时退回原始输出的 tail 长度（事件流全文太噪，
# 不喂 simulator）。
_STDOUT_TAIL_CHARS = 4000


def _extract_last_agent_text(jsonl_log: str) -> str:
    """取 codex --json 事件流里最后一条 agent message 文本（该轮输出）。

    codex 0.146 事件格式：``{"type":"item.completed","item":{"id":...,
    "type":"agent_message","text":"..."}}``（ThreadItem 是 serde flatten
    + tag="type"，见 exec/src/exec_events.rs）。
    """
    last_text = ""
    for line in jsonl_log.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        # item_type 兼容 app-server/SQLite 层命名；exec --json 实际是 type。
        item_type = item.get("type") or item.get("item_type")
        if item_type not in _AGENT_MESSAGE_ITEM_TYPES:
            continue
        text = item.get("text")
        if isinstance(text, str) and text:
            last_text = text
    return last_text


def _detect_recoverable_terminal_reason(log_text: str) -> str | None:
    """识别 codex --json 事件流里的可恢复终态，命中返回原因说明，否则 None。

    只认 _RECOVERABLE_EVENT_TYPES 里的事件；最后一条失败类事件（最靠近
    退出时刻）作为原因。事件流里没有任何 codex 事件时一律 None（基础设施
    类失败不可恢复）。
    """
    saw_codex_event = False
    reason: str | None = None
    for line in log_text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        etype = event.get("type")
        if not isinstance(etype, str):
            continue
        saw_codex_event = True
        if etype == "turn.failed":
            error = event.get("error")
            message = error.get("message") if isinstance(error, dict) else None
            reason = f"codex turn.failed（{message or '无 error message'}）"
        elif etype == "error":
            message = event.get("message")
            reason = f"codex error 事件（{message or '无 message'}）"
    if not saw_codex_event:
        return None
    return reason


class CodexUsersim(_LegacyCodex):
    """_LegacyCodex with a run_interactive_turn() user-simulation loop."""

    @staticmethod
    @override
    def name() -> str:
        return "codex-usersim"

    async def run_interactive_turn(
        self,
        user_message: str,
        environment: BaseEnvironment,
        context: AgentContext,
        session_id: str,
        turn_idx: int,
    ) -> str:
        if not self.model_name:
            raise ValueError("Model name is required")

        resume = turn_idx > 0
        # 每轮完整重放基类 setup：上一轮 finally 已清掉 $CODEX_HOME，
        # config.toml / auth 重写幂等；resume 轮由 setup 把 sessions 从
        # /logs/agent/sessions 拷回 $CODEX_HOME/sessions。
        env = await self._exec_codex_setup(environment, resume=resume)

        command_dir = self.logs_dir / f"command-{turn_idx + 1}"
        command_dir.mkdir(parents=True, exist_ok=True)
        command = self._build_exec_command(
            self.render_instruction(user_message), resume=resume
        )
        (command_dir / "command.txt").write_text(command)
        try:
            try:
                result = await self.exec_as_agent(environment, command=command, env=env)
            except NonZeroAgentExitCodeError as exc:
                # 可恢复终态（见文件头注释）：命中则把该轮部分输出交还
                # 模拟器，下一轮 resume --last 续跑同一会话；未命中
                # re-raise。
                reply = await self._recover_interactive_turn_failure(
                    environment=environment,
                    command_dir=command_dir,
                    exc=exc,
                )
            else:
                (command_dir / "return-code.txt").write_text(str(result.return_code))
                if result.stdout:
                    (command_dir / "stdout.txt").write_text(result.stdout)
                if result.stderr:
                    (command_dir / "stderr.txt").write_text(result.stderr)
                reply = self._turn_reply(result.stdout or result.stderr or "")
        finally:
            # 与基类 run() 的 finally 同款：sessions 每轮 cp 回
            # /logs/agent/sessions（下一轮 resume 的恢复源 + 本地
            # trajectory 的数据源），secrets/$CODEX_HOME 清掉。
            await self._persist_sessions(environment, env)
            await self._cleanup_codex_home(environment, env)

        await self._refresh_trajectory(environment, context)
        return reply

    def _turn_reply(self, log_text: str) -> str:
        """本轮回复：事件流最后一条 agent message；取不到退原始输出 tail。"""
        last_text = _extract_last_agent_text(log_text)
        if last_text:
            return last_text
        return log_text[-_STDOUT_TAIL_CHARS:]

    async def _recover_interactive_turn_failure(
        self,
        environment: BaseEnvironment,
        command_dir: Path,
        exc: NonZeroAgentExitCodeError,
    ) -> str:
        """run_interactive_turn 非零退出的可恢复终态判定。

        命中 turn.failed / error 类终态时返回该轮部分输出（不抛异常），
        否则 re-raise。
        """
        log_text = await self._read_turn_log(environment, exc)
        reason = _detect_recoverable_terminal_reason(log_text)
        if reason is None:
            raise exc

        # 现场 best-effort 落盘（对齐 ClaudeCodeUsersim）：command.txt
        # 调用前已写；return-code 从异常消息解析，stdout 用完整事件流，
        # stderr 用异常消息（含截断输出）。
        rc_match = re.search(r"\(exit (\d+)\)", str(exc))
        (command_dir / "return-code.txt").write_text(
            rc_match.group(1) if rc_match else "unknown"
        )
        (command_dir / "stdout.txt").write_text(log_text)
        (command_dir / "stderr.txt").write_text(str(exc))

        last_text = _extract_last_agent_text(log_text)
        header = f"[agent 本轮提前终止: {reason}]"
        return f"{header}\n\n{last_text}" if last_text else header

    async def _read_turn_log(
        self, environment: BaseEnvironment, exc: NonZeroAgentExitCodeError
    ) -> str:
        """回读容器内该轮完整事件流（/logs/agent/codex.txt）；读失败退用
        异常消息里的截断 stdout。

        异常消息中的 stdout 被 _truncate_output 截到 1000 字符，不足以做
        终态判定，所以优先 cat 容器里的完整 codex.txt。
        """
        log_path = (EnvironmentPaths.agent_dir / self._OUTPUT_FILENAME).as_posix()
        try:
            result = await environment.exec(f"cat {log_path}", user="root")
            if result.return_code == 0 and result.stdout:
                return result.stdout
        except Exception as read_exc:  # noqa: BLE001
            self.logger.debug(
                f"回读 turn 日志 {log_path} 失败，退用异常消息: {read_exc}"
            )
        return str(exc)

    async def _refresh_trajectory(
        self, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        """把 /logs/agent/sessions 的 session JSONL 转成 ATIF 写
        logs_dir/trajectory.json，并把累计 token/cost 记进 context。

        非 mounted 环境（远程 docker 模式 /logs/agent 不是 bind 挂载）先
        download_dir 拉回本地再转换。任何失败降级为 debug 日志，不炸轮次。
        """
        try:
            if not environment.capabilities.mounted:
                await environment.download_dir(
                    source_dir=(EnvironmentPaths.agent_dir / "sessions").as_posix(),
                    target_dir=self.logs_dir / "sessions",
                )
            session_dir = self._get_session_dir()
            if not session_dir:
                return
            trajectory = self._convert_events_to_trajectory(session_dir)
            if trajectory is None:
                return
            (self.logs_dir / "trajectory.json").write_text(
                format_trajectory_json(trajectory.to_json_dict())
            )
            if trajectory.final_metrics:
                metrics = trajectory.final_metrics
                context.cost_usd = metrics.total_cost_usd
                context.n_input_tokens = metrics.total_prompt_tokens or 0
                context.n_cache_tokens = metrics.total_cached_tokens or 0
                context.n_output_tokens = metrics.total_completion_tokens or 0
        except Exception as exc:  # noqa: BLE001
            self.logger.debug(
                f"codex usersim 每轮 trajectory 刷新失败（降级忽略）: {exc}"
            )
