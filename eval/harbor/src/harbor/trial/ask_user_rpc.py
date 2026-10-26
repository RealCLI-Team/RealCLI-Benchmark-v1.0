"""AskUserQuestion 中轮应答通道（2026-09-15）。

背景：usersim 交互评测中，被测 agent 调用 AskUserQuestion 工具时，无头模式
下没有真人应答。本模块把该工具的调用**中轮**路由给 user simulator：

  容器内 PreToolUse hook（settings 注入，CLI 协议已冒烟验证：deny+reason 同轮
  送达模型） → HTTP POST → 宿主机 socat 中继 → ssh -R 反向隧道 → harbor 进程
  内 RPC server → ScriptedUserSimulator.answer_tool_question() → 回答原路返回。

设计约束：
- 不改动 agent-sdk 与上游评测框架；改动只保留在 hook 注入层。
- hook 只注入到本 trial 的容器（managed-settings.json 或项目 .claude），
  不影响其他任务。
- 任何一环失败都降级为"无 hook"（CLI 默认行为）或"deny+引导纯文本提问"，
  绝不让 trial 因此崩溃。
- 环境变量 CLIBENCH_AUQ_HOOK=0 可整体关闭。
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import random
import re
import shlex
from typing import Any, Awaitable, Callable
from uuid import uuid4

# hook 在容器内的落点与 settings 注入点（冒烟验证后选定）
HOOK_SCRIPT_CONTAINER_PATH = "/tmp/.interaction/ask_user_hook.py"
MANAGED_SETTINGS_CONTAINER_PATH = "/etc/claude-code/managed-settings.json"
PROJECT_SETTINGS_CONTAINER_PATH = "/app/.claude/settings.json"

# hook 调 RPC 的超时（秒）。simulator 是一次 LLM 调用，可能几十秒；
# 必须小于 settings 里 hook 条目的 timeout（秒，CLI 侧硬上限）。
HOOK_HTTP_TIMEOUT_SEC = 200
HOOK_ENTRY_TIMEOUT_SEC = 240

# 宿主机 socat 监听端口范围（避开已知服务）
_HOST_PORT_RANGE = (41021, 59931)

AnswerCallback = Callable[[list[dict[str, Any]]], Awaitable[str]]


# ---------------------------------------------------------------------------
# 容器内 hook 脚本（模板，{port} 在注入时替换为宿主机 socat 端口）
# ---------------------------------------------------------------------------
HOOK_SCRIPT = r'''#!/usr/bin/env python3
"""PreToolUse hook for AskUserQuestion: forward the question to the user
simulator via the per-trial RPC relay, and deliver the simulator's answer to
the model as the (deny) reason — the answer arrives mid-turn, in-turn."""
import json
import os
import socket
import struct
import sys
import urllib.request

PORT = __PORT__
HOST_IP = "__HOST_IP__"  # 注入时烧入的宿主机 LAN IP（首选路径）


def _gateway() -> str | None:
    # 容器无 ip(8) 时从 /proc/net/route 解析默认网关（hex 小端）
    try:
        for line in open("/proc/net/route"):
            fields = line.split()
            if len(fields) > 2 and fields[1] == "00000000":
                return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
    except Exception:
        pass
    return None


def _targets() -> list[str]:
    override = os.environ.get("ASK_USER_RPC_ADDR", "").strip()
    if override:
        return [override]
    targets = [f"{HOST_IP}:{PORT}"]
    gw = _gateway()
    if gw:
        targets.append(f"{gw}:{PORT}")
    targets.append(f"172.17.0.1:{PORT}")
    return targets


def _reply(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    questions = (payload.get("tool_input") or {}).get("questions") or []
    fallback = (
        "AskUserQuestion is unavailable in this environment (no interactive UI). "
        "Ask your question in plain text at the end of your current response "
        "instead; the user will answer in their next message."
    )
    if not questions:
        _reply(fallback)
        return
    body = json.dumps({"questions": questions}).encode("utf-8")
    for target in _targets():
        try:
            req = urllib.request.Request(
                f"http://{target}/ask/__TOKEN__",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=__HTTP_TIMEOUT__) as resp:
                answer = json.loads(resp.read().decode("utf-8")).get("answer", "").strip()
            if not answer:
                raise ValueError("empty answer")
            _reply(
                "AskUserQuestion is unavailable in this environment, but the user "
                "saw your question and replied:\n"
                + answer
                + "\nTreat this as the user's answer and continue."
            )
            return
        except Exception:
            continue
    _reply(fallback)


if __name__ == "__main__":
    main()
'''


def render_hook_script(port: int, host_ip: str = "127.0.0.1", token: str = "") -> str:
    return (
        HOOK_SCRIPT.replace("__PORT__", str(port))
        .replace("__HOST_IP__", host_ip)
        .replace("__TOKEN__", token)
        .replace("__HTTP_TIMEOUT__", str(HOOK_HTTP_TIMEOUT_SEC))
    )


def hook_settings_json() -> str:
    return json.dumps(
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "AskUserQuestion",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"python3 {HOOK_SCRIPT_CONTAINER_PATH}",
                                "timeout": HOOK_ENTRY_TIMEOUT_SEC,
                            }
                        ],
                    }
                ]
            }
        },
        ensure_ascii=False,
        indent=2,
    )


def render_mailbox_hook(mailbox: str) -> str:
    """A sandbox has no SSH host relay; use per-request files via env.exec."""
    return """import json, sys, time, uuid
from pathlib import Path
root = Path(__MAILBOX__)
request_id = uuid.uuid4().hex
request = root / (request_id + ".request.json")
response = root / (request_id + ".answer.json")
fallback = "Ask your question in plain text at the end of this response; the user will reply next turn."
answer = fallback
try:
    questions = (json.load(sys.stdin).get("tool_input") or {}).get("questions") or []
    if questions:
        pending = request.with_suffix(".tmp")
        pending.write_text(json.dumps(questions))
        pending.replace(request)
        deadline = time.monotonic() + __TIMEOUT__
        while time.monotonic() < deadline:
            if response.exists():
                answer = json.loads(response.read_text()).get("answer") or fallback
                break
            time.sleep(0.2)
finally:
    request.unlink(missing_ok=True)
    response.unlink(missing_ok=True)
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": answer}}))
""".replace("__MAILBOX__", repr(mailbox)).replace(
        "__TIMEOUT__", str(HOOK_HTTP_TIMEOUT_SEC)
    )


# ---------------------------------------------------------------------------
# harbor 进程内 RPC server（asyncio，与被测 agent 的 await 共用事件循环——
# 只有 agent 轮进行中才会有请求进来，此时 simulator 必然空闲）
# ---------------------------------------------------------------------------
class AskUserRpcServer:
    def __init__(self, answer_cb: AnswerCallback, logger: logging.Logger, token: str):
        self._answer_cb = answer_cb
        self._log = logger
        self._token = token
        self._server: asyncio.AbstractServer | None = None
        self.port: int | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle, host="127.0.0.1", port=0
        )
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        except Exception:
            writer.close()
            return
        try:
            first = head.split(b"\r\n", 1)[0].decode("latin1")
            method, path, _ = first.split(" ", 2)
            length = 0
            for line in head.split(b"\r\n")[1:]:
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":", 1)[1].strip())
            body = await asyncio.wait_for(reader.readexactly(length), 10)
        except Exception:
            await self._send(writer, 400, {"error": "bad request"})
            return

        if method == "GET" and path == f"/health/{self._token}":
            await self._send(writer, 200, {"ok": True})
            return
        if method != "POST" or path != f"/ask/{self._token}":
            await self._send(writer, 404, {"error": "not found"})
            return
        try:
            payload = json.loads(body.decode("utf-8"))
            questions = payload.get("questions") or []
            if not isinstance(questions, list):
                raise ValueError("questions must be a list")
        except Exception:
            await self._send(writer, 400, {"error": "bad json"})
            return
        try:
            answer = await self._answer_cb(questions)
            await self._send(writer, 200, {"answer": answer})
        except Exception as exc:  # noqa: BLE001
            self._log.warning("ask_user_rpc answer failed: %s", exc)
            await self._send(writer, 500, {"error": f"{type(exc).__name__}: {exc}"})

    @staticmethod
    async def _send(
        writer: asyncio.StreamWriter, status: int, obj: dict[str, Any]
    ) -> None:
        reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 500: "ERR"}[status]
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        writer.write(
            f"HTTP/1.0 {status} {reason}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(data)}\r\n\r\n".encode("latin1")
            + data
        )
        with contextlib.suppress(Exception):
            await writer.drain()
        writer.close()


# ---------------------------------------------------------------------------
# ssh -R 反向隧道 + 宿主机 socat 中继
# （宿主机 sshd GatewayPorts=no，-R 只能绑 localhost，故用 socat 把
#   0.0.0.0:Q 转到 127.0.0.1:P；ssh 会话死亡时 socat 随 SIGHUP 退出）
# ---------------------------------------------------------------------------
_DOCKER_HOST_RE = re.compile(r"^tcp://([^:]+):(\d+)$")


def _pdeathsig() -> None:
    """PR_SET_PDEATHSIG=SIGKILL：父进程一死本子进程立即被杀（preexec_fn）。"""
    import ctypes

    libc = ctypes.CDLL(None)
    # PR_SET_PDEATHSIG = 1, SIGKILL = 9
    libc.prctl(1, 9)


class SshRelay:
    def __init__(self, host: str, local_port: int, logger: logging.Logger, token: str):
        self._host = host
        self._local_port = local_port
        self._log = logger
        self._token = token
        self._proc: asyncio.subprocess.Process | None = None
        self.host_port: int | None = None  # 容器应访问的宿主机端口（socat Q）

    async def start(self, *, timeout_sec: float = 20) -> None:
        ssh_pass = os.environ.get("SSH_PASS", "").strip()
        if not ssh_pass:
            raise RuntimeError("SSH_PASS 未设置，无法建立 ask-user 隧道")
        rng = random.Random()
        last_err = ""
        for _ in range(5):
            p_inner = rng.randint(*_HOST_PORT_RANGE)
            q_outer = rng.randint(*_HOST_PORT_RANGE)
            if p_inner == q_outer:
                continue
            # 远端命令用 stdin 保活：socat 后台跑，shell 阻塞在 read 上；
            # 本地 ssh 一死（含 pdeathsig 被杀），通道 stdin EOF → read 返回
            # → socat 被杀。防止任何一端的孤儿残留（2026-09-15 实测泄漏）。
            remote_cmd = (
                f"socat TCP-LISTEN:{q_outer},bind=0.0.0.0,reuseaddr,fork "
                f"TCP:127.0.0.1:{p_inner} & SPID=$!; "
                f"read; kill $SPID 2>/dev/null"
            )
            cmd = [
                "sshpass",
                "-p",
                ssh_pass,
                "ssh",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                "-o",
                "ExitOnForwardFailure=yes",
                "-R",
                f"127.0.0.1:{p_inner}:127.0.0.1:{self._local_port}",
                f"root@{self._host}",
                remote_cmd,
            ]
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                # stdin 管道保持敞开（永不写/关）：它是远端 read 保活的生命线
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                # 父进程死亡（含看门狗 SIGTERM/SIGKILL）时 ssh 子进程随之死
                preexec_fn=_pdeathsig,
            )
            # 等隧道+中继就绪：从本机直连宿主机 Q 口应能打到 RPC /health
            deadline = asyncio.get_event_loop().time() + timeout_sec
            while asyncio.get_event_loop().time() < deadline:
                if self._proc.returncode is not None:
                    stderr = (await self._proc.stderr.read()).decode("utf-8", "replace")
                    last_err = f"ssh exited rc={self._proc.returncode}: {stderr[-300:]}"
                    break
                try:
                    r, w = await asyncio.wait_for(
                        asyncio.open_connection(self._host, q_outer), 3
                    )
                    w.write(
                        f"GET /health/{self._token} HTTP/1.0\r\nHost: x\r\n\r\n".encode()
                    )
                    await w.drain()
                    resp = await asyncio.wait_for(r.read(256), 3)
                    w.close()
                    if b'"ok": true' in resp or b'"ok":true' in resp:
                        self.host_port = q_outer
                        return
                except Exception:
                    await asyncio.sleep(1)
            # 本轮端口不可用，杀掉重试
            with contextlib.suppress(Exception):
                self._proc.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._proc.wait(), 5)
            self._proc = None
        raise RuntimeError(f"ask-user 隧道建立失败: {last_err or 'health check 超时'}")

    async def stop(self) -> None:
        if self._proc is None:
            return
        with contextlib.suppress(Exception):
            self._proc.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._proc.wait(), 5)
        self._proc = None


# ---------------------------------------------------------------------------
# 组装：server + tunnel + 容器 hook 注入
# ---------------------------------------------------------------------------
class AskUserHookService:
    """一个 trial 的 AskUserQuestion 应答服务全生命周期。"""

    def __init__(
        self,
        answer_cb: AnswerCallback,
        environment: Any,
        logger: logging.Logger,
    ):
        self._answer_cb = answer_cb
        self._env = environment
        self._log = logger
        # 每 trial 随机 token 放进 URL 路径：宿主机上其他 trial 的陈旧/同端口
        # socat 中继不会把问题错路由到本 trial 的 simulator（2026-09-15 防串）。
        self._token = uuid4().hex[:16]
        self._server: AskUserRpcServer | None = None
        self._relay: SshRelay | None = None
        self._mailbox_task: asyncio.Task[None] | None = None

    async def _poll_mailbox(self, mailbox: str) -> None:
        read_code = (
            "import json; from pathlib import Path; "
            f"root=Path({mailbox!r}); "
            "print(json.dumps({p.stem.split('.')[0]:json.loads(p.read_text()) "
            "for p in root.glob('*.request.json')}))"
        )
        seen: set[str] = set()
        while True:
            try:
                result = await self._env.exec(
                    "python3 -c " + shlex.quote(read_code),
                    user="root",
                    timeout_sec=30,
                )
                if result.return_code != 0:
                    raise RuntimeError("Cannot read AskUserQuestion mailbox")
                for request_id, questions in json.loads(result.stdout or "{}").items():
                    if request_id in seen or not re.fullmatch(
                        r"[0-9a-f]{32}", request_id
                    ):
                        continue
                    if not isinstance(questions, list) or not all(
                        isinstance(q, dict) for q in questions
                    ):
                        continue
                    seen.add(request_id)
                    try:
                        answer = await asyncio.wait_for(
                            self._answer_cb(questions), HOOK_HTTP_TIMEOUT_SEC - 10
                        )
                    except Exception:
                        self._log.warning("AskUserQuestion simulator failed")
                        answer = "Please ask again in plain text at the end of this response."
                    payload = json.dumps({"answer": answer})
                    write_code = (
                        "from pathlib import Path; "
                        f"p=Path({(mailbox + '/' + request_id + '.answer.json')!r}); "
                        f"tmp=p.with_suffix('.tmp'); tmp.write_text({payload!r}); "
                        "tmp.chmod(0o644); tmp.replace(p)"
                    )
                    written = await self._env.exec(
                        "python3 -c " + shlex.quote(write_code),
                        user="root",
                        timeout_sec=30,
                    )
                    if written.return_code != 0:
                        self._log.warning("Cannot write AskUserQuestion answer")
            except Exception as exc:
                self._log.warning(
                    "AskUserQuestion mailbox poll failed: %s", type(exc).__name__
                )
            await asyncio.sleep(2)

    async def start(self) -> None:
        docker_host = os.environ.get("DOCKER_HOST", "").strip()
        match = _DOCKER_HOST_RE.match(docker_host)
        if not match:
            self._log.info(
                "ask_user_hook: DOCKER_HOST=%r 非远程 tcp，跳过 hook 注入",
                docker_host,
            )
            return
        host = match.group(1)

        self._server = AskUserRpcServer(self._answer_cb, self._log, self._token)
        await self._server.start()
        assert self._server.port is not None

        self._relay = SshRelay(host, self._server.port, self._log, self._token)
        await self._relay.start()
        assert self._relay.host_port is not None

        await self._inject_hook(self._relay.host_port, host)
        self._log.info(
            "ask_user_hook: 已注入（容器 hook → %s:%s → RPC :%s）",
            host,
            self._relay.host_port,
            self._server.port,
        )

    async def stop(self) -> None:
        if self._mailbox_task is not None:
            self._mailbox_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._mailbox_task
            self._mailbox_task = None
        if self._relay is not None:
            await self._relay.stop()
        if self._server is not None:
            await self._server.stop()

    async def _inject_hook(
        self, host_port: int = 0, host_ip: str = "", *, script: str | None = None
    ) -> None:
        if script is None:
            script = render_hook_script(host_port, host_ip, self._token)
        settings = hook_settings_json()

        async def _write(path: str, content: str, mode: str = "0644") -> None:
            quoted = shlex.quote(path)
            # base64 传输避免引号/转义问题
            b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
            result = await self._env.exec(
                f"mkdir -p $(dirname {quoted}) && "
                f"echo {shlex.quote(b64)} | base64 -d > {quoted} && "
                f"chmod {mode} {quoted}",
                user="root",
                timeout_sec=30,
            )
            if result.return_code != 0:
                raise RuntimeError(
                    f"写入 {path} 失败 rc={result.return_code}: "
                    f"{(result.stderr or '')[-200:]}"
                )

        await _write(HOOK_SCRIPT_CONTAINER_PATH, script, mode="0755")
        # 注入点（冒烟验证）：/etc/claude-code/managed-settings.json 对企业级
        # 托管设置全 CLI 生效，且对容器内 agent 不可见（不在 /app 工作区）。
        await _write(MANAGED_SETTINGS_CONTAINER_PATH, settings)
