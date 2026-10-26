"""Claude Code agent variant for clibench user-simulation evaluation.

Subclass of the legacy (pre-SDK) ClaudeCode implementation with two patches:

1. ``_build_run_command`` writes the stream-json log via shell redirection +
   ``cat`` instead of ``| tee``. Agents that spawn background processes
   (http.server previews, nohup jobs, ...) make those children inherit stdout
   and hold the tee pipe open, so ``docker exec`` never sees EOF and hangs
   until the watchdog kills it. With redirection, bash only waits for the
   main claude process; background children holding the file fd are harmless.
   The exit code is propagated explicitly (``rc=$?; ...; exit $rc``).

2. ``run_interactive_turn`` treats recoverable terminal states (API refusal
   ``stop_reason=refusal`` / Usage Policy, SDK hitting ``max_turns``) as a
   partially completed turn: the last assistant text is handed back to the
   user simulator instead of raising through the interaction loop and killing
   the whole trial. The next turn resumes the same session, so the
   conversation continues naturally. Non-recoverable exits re-raise.

Use via agent import_path:
    harbor.agents.installed.claude_code_usersim:ClaudeCodeUsersim
"""

import json
import re
import shlex
from pathlib import Path
from typing import override

from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.agents.installed.claude_code_legacy import (
    ClaudeCode as LegacyClaudeCode,
)
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

# 可恢复终态标记（2026-09-07）：claude --print 非零退出时，若该轮
# stream-json 日志里出现以下字样，说明是 API 拒答或 SDK 打满 max_turns，
# 会话本身未损坏，交互循环可拿该轮部分输出继续（下一轮 --resume 同一会话）。
_REFUSAL_MARKERS = (
    '"stop_reason":"refusal"',
    '"stop_reason": "refusal"',
    "Usage Policy",
)
_MAX_TURNS_MARKERS = (
    '"terminal_reason":"max_turns"',
    '"terminal_reason": "max_turns"',
    "error_max_turns",
)
# 网关/上游断流：与拒答同属
# 瞬时可重试终态。
_API_ERROR_MARKERS = (
    '"terminal_reason":"api_error"',
    '"terminal_reason": "api_error"',
    "API Error: Upstream stream failed",
    '"api_error_status":',
    # 网关间歇返回 HTTP 200 但体非合法 Message：轮次语义上是瞬时错误，可重试。
    "API returned an empty or malformed response",
)


def _detect_recoverable_terminal_reason(log_text: str) -> str | None:
    """识别 stream-json 日志里的可恢复终态，命中返回原因说明，否则 None。"""
    if any(marker in log_text for marker in _REFUSAL_MARKERS):
        return "API 拒答（stop_reason=refusal / Usage Policy）"
    if any(marker in log_text for marker in _MAX_TURNS_MARKERS):
        return "达到 max_turns 上限（terminal_reason=max_turns）"
    if any(marker in log_text for marker in _API_ERROR_MARKERS):
        return "API 断流（terminal_reason=api_error / upstream stream failed）"
    return None


def _retryable(reason: str | None) -> bool:
    """该终态值不值得原样重试：拒答/断流是瞬时抖动，重试可能过；
    max_turns 是预算耗尽，重试纯浪费。"""
    return reason is not None and ("拒答" in reason or "断流" in reason)


def _extract_last_assistant_text(stream_json_log: str) -> str:
    """取 stream-json 日志里最后一个 assistant 文本消息（该轮部分输出）。"""
    last_text = ""
    for line in stream_json_log.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") != "assistant":
            continue
        content = (event.get("message") or {}).get("content") or []
        texts = [
            block.get("text") or ""
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "\n".join(t for t in texts if t)
        if text:
            last_text = text
    return last_text


class ClaudeCodeUsersim(LegacyClaudeCode):
    """Legacy ClaudeCode with usersim-specific resilience patches."""

    @staticmethod
    @override
    def name() -> str:
        return "claude-code-usersim"

    @override
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
        # 2026-09-02：`| tee` 改「重定向 + cat」——agent 起的后台进程（http.server
        # 预览、nohup grinder 等）会继承 stdout 占住管道，tee 永远等不到 EOF，
        # docker exec 挂死直到看门狗 SIGTERM（v0.3 批 8+ 例实测）。重定向到文件后
        # bash 只等 claude 主进程退出，后台子进程持有文件 fd 不影响；rc 显式透传
        # （外层 base.py 有 set -o pipefail，这里不依赖管道语义）。
        return (
            'export PATH="$HOME/.local/bin:$PATH"; '
            f"claude --verbose --output-format=stream-json "
            f"{extra_flags}"
            f"{settings_flags}"
            f"{session_flags}"
            f"--print {prompt_input} > {shlex.quote(log_path)} 2>&1; "
            f"rc=$?; cat {shlex.quote(log_path)}; exit $rc"
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
        env = self._build_env_vars()
        if turn_idx == 0:
            setup_command = self._build_setup_command()
            await self.exec_as_agent(environment, command=setup_command, env=env)

        command_dir = self.logs_dir / f"command-{turn_idx + 1}"
        command_dir.mkdir(parents=True, exist_ok=True)
        base_log_path = f"/logs/agent/claude-code-turn-{turn_idx + 1}.txt"
        # 瞬时可重试终态（API 拒答/断流）自动重试该轮：同 session resume、
        # 同一用户消息重发，最多 2 次重试；仍不行走原有的部分输出交还兜底。
        # max_turns 终态不重试（预算耗尽，重试纯浪费）。重试轮写独立日志
        # （turn-N-retryK.txt），command-N/ 下保留最后一次的 rc/stdout。
        for attempt in range(3):
            log_path = (
                base_log_path
                if attempt == 0
                else base_log_path.replace(".txt", f"-retry{attempt}.txt")
            )
            command = await self._prepare_run_command(
                environment,
                self.render_instruction(user_message),
                log_path=log_path,
                session_id=session_id,
                # 重试时必须 --resume：首轮（turn_idx=0）第一次尝试已用
                # --session-id 建过会话，重试再带 --session-id 会同 id 撞
                # "Session ID already in use"（2026-09-17 sandbox 冒烟实测）。
                resume=turn_idx > 0 or attempt > 0,
            )
            (command_dir / "command.txt").write_text(command)
            try:
                result = await self.exec_as_agent(environment, command=command, env=env)
            except NonZeroAgentExitCodeError as exc:
                # 可恢复终态不抛穿；拒答/断流先自动重试，重试耗尽才落
                # 部分输出兜底。
                log_text = await self._read_turn_log(environment, log_path, exc)
                reason = _detect_recoverable_terminal_reason(log_text)
                if reason is None:
                    raise
                if _retryable(reason) and attempt < 2:
                    self.logger.warning(
                        f"turn {turn_idx + 1} 第 {attempt + 1} 次尝试失败（{reason}），"
                        "原样重试该轮"
                    )
                    continue
                return await self._recover_interactive_turn_failure(
                    environment=environment,
                    command_dir=command_dir,
                    log_path=log_path,
                    exc=exc,
                    log_text=log_text,
                    retried=attempt,
                )
            (command_dir / "return-code.txt").write_text(str(result.return_code))
            if result.stdout:
                (command_dir / "stdout.txt").write_text(result.stdout)
            if result.stderr:
                (command_dir / "stderr.txt").write_text(result.stderr)
            return result.stdout or result.stderr or ""
        raise AssertionError("unreachable: 重试循环必有返回")

    async def _recover_interactive_turn_failure(
        self,
        environment: BaseEnvironment,
        command_dir: Path,
        log_path: str,
        exc: NonZeroAgentExitCodeError,
        log_text: str | None = None,
        retried: int = 0,
    ) -> str:
        """run_interactive_turn 非零退出的可恢复终态判定。

        命中拒答 / max_turns / 断流时返回该轮部分输出（不抛异常），否则
        re-raise。log_text 已读则直接用（调用方判定过 reason），retried>0
        说明是重试耗尽后的兜底，header 里标注。
        """
        if log_text is None:
            log_text = await self._read_turn_log(environment, log_path, exc)
        reason = _detect_recoverable_terminal_reason(log_text)
        if reason is None:
            raise exc

        # 现场 best-effort 落盘：command.txt 调用前已写；return-code 从异常
        # 消息解析，stdout 用完整日志，stderr 用异常消息（含截断输出）。
        rc_match = re.search(r"\(exit (\d+)\)", str(exc))
        (command_dir / "return-code.txt").write_text(
            rc_match.group(1) if rc_match else "unknown"
        )
        (command_dir / "stdout.txt").write_text(log_text)
        (command_dir / "stderr.txt").write_text(str(exc))

        last_text = _extract_last_assistant_text(log_text)
        retry_note = f"（已自动重试 {retried} 次仍失败）" if retried else ""
        header = f"[agent 本轮提前终止: {reason}{retry_note}]"
        return f"{header}\n\n{last_text}" if last_text else header

    async def _read_turn_log(
        self,
        environment: BaseEnvironment,
        log_path: str,
        exc: NonZeroAgentExitCodeError,
    ) -> str:
        """回读容器内该轮完整日志；读失败退而用异常消息里的截断 stdout。

        异常消息中的 stdout 被 _truncate_output 截到 1000 字符，不足以做
        终态判定，所以优先 cat 容器里的完整 turn 日志。
        """
        try:
            result = await environment.exec(f"cat {log_path}", user="root")
            if result.return_code == 0 and result.stdout:
                return result.stdout
        except Exception as read_exc:  # noqa: BLE001
            self.logger.debug(
                f"回读 turn 日志 {log_path} 失败，退用异常消息: {read_exc}"
            )
        return str(exc)
