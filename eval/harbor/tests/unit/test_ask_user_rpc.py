"""ask_user_rpc 的单测（2026-09-15 AskUserQuestion 中轮应答通道）。

覆盖：RPC server 路由/错误分支、渲染后的 hook 脚本端到端（真起 server +
子进程跑 hook）、隧道参数构造。不依赖 docker/ssh/真实 LLM。
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from types import SimpleNamespace


from harbor.trial.ask_user_rpc import (
    AskUserHookService,
    AskUserRpcServer,
    HOOK_HTTP_TIMEOUT_SEC,
    HOOK_SCRIPT_CONTAINER_PATH,
    hook_settings_json,
    render_hook_script,
    render_mailbox_hook,
)


async def test_sandbox_mailbox_roundtrip(tmp_path):
    class LocalEnvironment:
        async def exec(self, command, **kwargs):
            proc = await asyncio.create_subprocess_shell(
                command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            out, err = await proc.communicate()
            return SimpleNamespace(
                return_code=proc.returncode, stdout=out.decode(), stderr=err.decode()
            )

    seen = []

    async def answer(questions):
        seen.append(questions)
        return "Choose A; keep the original files."

    service = AskUserHookService(answer, LocalEnvironment(), _Log())
    script = tmp_path / "hook.py"
    script.write_text(render_mailbox_hook(str(tmp_path)))
    service._mailbox_task = asyncio.create_task(service._poll_mailbox(str(tmp_path)))
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(script),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(
                json.dumps(
                    {"tool_input": {"questions": [{"question": "A or B?"}]}}
                ).encode()
            ),
            timeout=10,
        )
        assert proc.returncode == 0, err
        assert (
            json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
            == "Choose A; keep the original files."
        )
        assert seen == [[{"question": "A or B?"}]]
        assert not list(tmp_path.glob("*.request.json"))
        assert not list(tmp_path.glob("*.answer.json"))
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        await service.stop()
    assert service._mailbox_task is None


class _Log:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, *args: object) -> None:
        self.warnings.append(" ".join(str(a) for a in args))

    def info(self, *args: object) -> None:
        pass


async def _post(port: int, path: str, payload: dict) -> tuple[int, dict]:
    import urllib.request

    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    def _do() -> tuple[int, dict]:
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    return await asyncio.to_thread(_do)


async def test_rpc_roundtrip() -> None:
    seen: list[list[dict]] = []

    async def answer(questions: list[dict]) -> str:
        seen.append(questions)
        return "choose option A"

    server = AskUserRpcServer(answer, _Log(), "t0ken")
    await server.start()
    assert server.port is not None
    try:
        status, body = await _post(
            server.port, "/ask/t0ken", {"questions": [{"question": "q?"}]}
        )
        assert status == 200
        assert body == {"answer": "choose option A"}
        assert seen == [[{"question": "q?"}]]
    finally:
        await server.stop()


async def test_rpc_error_branches() -> None:
    async def boom(_questions: list[dict]) -> str:
        raise RuntimeError("simulator down")

    server = AskUserRpcServer(boom, _Log(), "t0ken")
    await server.start()
    assert server.port is not None
    try:
        status, body = await _post(server.port, "/nope", {})
        assert status == 404
        status, body = await _post(server.port, "/ask/t0ken", {"questions": "notalist"})
        assert status == 400
        status, body = await _post(server.port, "/ask/t0ken", {"questions": []})
        assert status == 500
        assert "simulator down" in body["error"]
    finally:
        await server.stop()


def test_render_hook_script_has_port_and_timeout() -> None:
    script = render_hook_script(45555)
    assert "PORT = 45555" in script
    assert f"timeout={HOOK_HTTP_TIMEOUT_SEC}" in script
    assert "__PORT__" not in script and "__HTTP_TIMEOUT__" not in script


def test_hook_settings_json_shape() -> None:
    settings = json.loads(hook_settings_json())
    entry = settings["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == "AskUserQuestion"
    assert HOOK_SCRIPT_CONTAINER_PATH in entry["hooks"][0]["command"]
    assert entry["hooks"][0]["timeout"] > HOOK_HTTP_TIMEOUT_SEC


def _run_hook(tmp_path, addr: str, payload: dict, token: str = "") -> dict:
    hook = tmp_path / "hook.py"
    hook.write_text(render_hook_script(int(addr.rsplit(":", 1)[1]), token=token))
    result = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        env={"ASK_USER_RPC_ADDR": addr, "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_hook_script_end_to_end(tmp_path) -> None:
    async def answer(questions: list[dict]) -> str:
        q = questions[0]["question"]
        return f"user says: {q} -> A alpha"

    async def _serve() -> int:
        server = AskUserRpcServer(answer, _Log(), "t0ken")
        await server.start()
        assert server.port is not None
        return server.port

    # 在后台线程跑事件循环，hook 子进程同步访问
    loop = asyncio.new_event_loop()
    port = loop.run_until_complete(_serve())

    import threading

    def _spin() -> None:
        loop.run_forever()

    thread = threading.Thread(target=_spin, daemon=True)
    thread.start()
    try:
        out = _run_hook(
            tmp_path,
            f"127.0.0.1:{port}",
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [{"question": "which one?", "options": []}]
                },
            },
            token="t0ken",
        )
        hook_out = out["hookSpecificOutput"]
        assert hook_out["hookEventName"] == "PreToolUse"
        assert hook_out["permissionDecision"] == "deny"
        assert (
            "user says: which one? -> A alpha" in hook_out["permissionDecisionReason"]
        )
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)


def test_hook_script_fallback_on_dead_rpc(tmp_path) -> None:
    out = _run_hook(
        tmp_path,
        "127.0.0.1:1",  # 不可达
        {"tool_input": {"questions": [{"question": "q?"}]}},
    )
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "unavailable" in reason
    assert "plain text" in reason


def test_hook_script_bad_stdin(tmp_path) -> None:
    hook = tmp_path / "hook.py"
    hook.write_text(render_hook_script(45555))
    result = subprocess.run(
        [sys.executable, str(hook)],
        input="not json",
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0
    out = json.loads(result.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
