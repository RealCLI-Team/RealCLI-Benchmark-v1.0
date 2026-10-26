"""claude-code agent 会话后端：用持久 agent 会话替代每轮无状态 LLM 调用。

动机：无状态单发模式每轮要把 persona + 全部信息片 + 全部探针 + 完整
对话历史 + 输出协议塞进一个 prompt（几万字符），剧本稍大就容易空输出/
截断。agent 会话模式下静态简报只在首轮发一次，后续每轮只发增量（agent
最新回复 + 必要指令），会话状态由 claude session 维护。

安全约束：模拟器 agent 只允许读写自己的本地进度账本；cwd 为独立临时目录，
与被测 agent 的容器环境完全隔离。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# 常见安装位置；可用 HARBOR_USER_SIM_CLAUDE_BIN 覆盖。
_CLAUDE_BIN_CANDIDATES = (
    "/usr/local/bin/claude",
    "/usr/bin/claude",
)

# 每轮调用超时（秒）。会话重置后重试要重新 ingest 全剧本+对话史，300s 实测不够。
_DEFAULT_TURN_TIMEOUT_SEC = 900.0

# runner 类型：给定 argv/stdin 文本/cwd/env/timeout，返回 (returncode, stdout)。
Runner = Callable[
    [list[str], str, str, dict[str, str], float], Awaitable[tuple[int, str]]
]


def _default_claude_bin() -> str:
    override = os.environ.get("HARBOR_USER_SIM_CLAUDE_BIN")
    if override:
        return override
    for c in _CLAUDE_BIN_CANDIDATES:
        if Path(c).exists():
            return c
    found = shutil.which("claude")
    if found:
        return found
    raise RuntimeError("未找到 claude CLI：请设置 HARBOR_USER_SIM_CLAUDE_BIN 环境变量")


def _turn_timeout_sec(env: dict[str, str]) -> float:
    """Read the simulator's per-CLI-call timeout without changing the default."""
    raw = env.get("HARBOR_USER_SIM_TURN_TIMEOUT_SEC")
    if raw is None:
        return _DEFAULT_TURN_TIMEOUT_SEC
    try:
        timeout = float(raw)
    except ValueError:
        logger.warning(
            "invalid HARBOR_USER_SIM_TURN_TIMEOUT_SEC=%r; using %.0fs",
            raw,
            _DEFAULT_TURN_TIMEOUT_SEC,
        )
        return _DEFAULT_TURN_TIMEOUT_SEC
    if timeout <= 0:
        logger.warning(
            "HARBOR_USER_SIM_TURN_TIMEOUT_SEC must be positive, got %r; using %.0fs",
            raw,
            _DEFAULT_TURN_TIMEOUT_SEC,
        )
        return _DEFAULT_TURN_TIMEOUT_SEC
    return timeout


async def _subprocess_runner(
    argv: list[str],
    input_text: str,
    cwd: str,
    env: dict[str, str],
    timeout: float,
) -> tuple[int, str]:
    """默认 runner：异步子进程跑 claude -p（prompt 经 stdin 传入），
    超时杀进程。"""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(
            proc.communicate(input=input_text.encode("utf-8")),
            timeout=timeout,
        )
    except (asyncio.TimeoutError, TimeoutError):
        proc.kill()
        await proc.wait()
        return -9, ""
    return proc.returncode or 0, out.decode("utf-8", errors="replace")


class ClaudeAgentSession:
    """一个 trial 一个会话：首轮 --session-id 建会话，之后 --resume 增量。

    环境变量需求（由 pipeline 在启动 harbor 前导出，与 pipeline 其他 agent
    一致）：ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN /
    ANTHROPIC_MODEL；root 下需 IS_SANDBOX=1。
    """

    def __init__(
        self,
        *,
        claude_bin: str | None = None,
        workdir: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        runner: Runner | None = None,
    ) -> None:
        self._bin = claude_bin or _default_claude_bin()
        if workdir is None:
            workdir = Path(tempfile.mkdtemp(prefix="harbor-usersim-sess-"))
        self._workdir = workdir
        self._workdir.mkdir(parents=True, exist_ok=True)
        self._env = dict(env if env is not None else os.environ)
        # Platform routing owns the controller's ANTHROPIC_* variables. Keep
        # the simulator on its own endpoint/model when evaluating another model.
        #
        # HARBOR_USER_SIM_ANTHROPIC_* is the original low-level interface.
        # The platform templates use the shorter HARBOR_USER_SIM_AGENT_* names;
        # accept both so an OpenAI-routed evaluated agent cannot leave the
        # Claude CLI simulator without an Anthropic endpoint or credential.
        for key in (
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_MODEL",
        ):
            override_value = self._env.get(f"HARBOR_USER_SIM_{key}")
            if not override_value:
                agent_key = {
                    "ANTHROPIC_BASE_URL": "HARBOR_USER_SIM_AGENT_BASE_URL",
                    "ANTHROPIC_API_KEY": "HARBOR_USER_SIM_AGENT_API_KEY",
                    "ANTHROPIC_AUTH_TOKEN": "HARBOR_USER_SIM_AGENT_API_KEY",
                    "ANTHROPIC_MODEL": "HARBOR_USER_SIM_AGENT_MODEL",
                }[key]
                override_value = self._env.get(agent_key)
            if override_value:
                self._env[key] = override_value
        self._env.setdefault("IS_SANDBOX", "1")
        # 会话 transcript 放在容器可写层之外，避免占满临时空间。
        self._env.setdefault(
            "CLAUDE_CONFIG_DIR",
            tempfile.mkdtemp(prefix="harbor-usersim-claude-config-"),
        )
        self._timeout = _turn_timeout_sec(self._env) if timeout is None else timeout
        self._runner = runner or _subprocess_runner
        self._session_id = str(uuid.uuid4())
        self._started = False

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def workdir(self) -> Path:
        """Directory visible to the simulator agent's local file tools."""
        return self._workdir

    async def call(self, prompt: str) -> str:
        """发一轮消息，返回 result 文本；任何失败返回空串（调用方走
        empty_message 重试/兜底/熔断逻辑）。

        prompt 经 stdin 传入而非 -p 参数：Linux 单条 argv 上限 128KB
        （MAX_ARG_STRLEN），大剧本静态简报超限时 execve 直接 E2BIG
        （2026-08-31 threejs r5 round0 实测 'Argument list too long'）。"""
        argv = [
            self._bin,
            "-p",
            "--output-format",
            "json",
            # The simulator owns simulator_progress.jsonl in this isolated
            # directory. It may read/write that file, but cannot run commands
            # or access the target agent's task environment.
            "--tools",
            "Read,Write,Edit",
            "--permission-mode",
            "acceptEdits",
        ]
        if self._started:
            argv += ["--resume", self._session_id]
        else:
            argv += ["--session-id", self._session_id]
        try:
            rc, out = await self._runner(
                argv, prompt, str(self._workdir), self._env, self._timeout
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("user-sim agent call raised: %r", e)
            self._reset_session()
            return ""
        if rc != 0:
            logger.warning("user-sim agent rc=%d, out tail=%r", rc, out[-300:])
            # rc!=0 后会话状态不可信（下一轮再用同一 id 会撞
            # "Session ID already in use"）→ 换新会话 id 重来
            self._reset_session()
            return ""
        try:
            envelope: dict[str, Any] = json.loads(out)
        except (json.JSONDecodeError, TypeError, ValueError):
            # 容错解析：新版 claude 可能在 JSON 外打印警告/杂行（stderr 合并进
            # stdout），取最后一个完整 JSON 对象；解析不出同样换会话重来
            envelope = self._parse_last_json_object(out)
            if envelope is None:
                logger.warning(
                    "user-sim agent output not JSON envelope: %r", out[-300:]
                )
                self._reset_session()
                return ""
            logger.info("user-sim agent envelope recovered from noisy output")
        if envelope.get("is_error"):
            logger.warning(
                "user-sim agent is_error: %r", str(envelope.get("result"))[-300:]
            )
            self._reset_session()
            return ""
        self._started = True
        return str(envelope.get("result") or "")

    def _reset_session(self) -> None:
        """调用失败后换全新会话 id（避免复用已损坏/占用会话的级联故障）。"""
        self._session_id = str(uuid.uuid4())
        self._started = False

    @staticmethod
    def _parse_last_json_object(text: str) -> dict[str, Any] | None:
        """从可能含杂行的输出里取最后一个完整 JSON 对象。"""
        decoder = json.JSONDecoder()
        last = None
        for i, ch in enumerate(text):
            if ch != "{":
                continue
            try:
                obj, _ = decoder.raw_decode(text[i:])
            except ValueError:
                continue
            if isinstance(obj, dict):
                last = obj
        return last
