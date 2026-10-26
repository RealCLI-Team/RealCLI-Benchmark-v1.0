"""Agent-based simulated user for CliBench script.json.

The simulator writes one decision to ``simulator_turn.json``. The runner
validates it and owns the append-only ``simulator_progress.jsonl`` ledger. This adapter waits for the agent call to
finish, reads the latest simulator-turn record, and forwards its message.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from harbor.trial.probe_protocol import (
    cancelled_slice_ids,
    delivered_probe_ids,
    message_matches_probe,
    requires_delivery,
    stimulus_anchor,
    unreleased_slice_ids,
)
from harbor.trial.sim_agent_backend import ClaudeAgentSession

logger = logging.getLogger(__name__)

# runner 写入账本的字符串字段上限（字符）。账本是模拟器唯一的持久状态，
# 模拟器只能用 Read/Write/Edit 读它，而 Read 有硬限制（单次 25000 tokens /
# 256KB）：2026-09-16 airflow-upstream-partition-hold / batch-record-format-
# converter 两次 simulator_broken 实测，未截断的 target_agent_reply 单行
# 达 247KB（>11 万 tokens），模拟器冷启动恢复时读不了账本尾部，只能盲猜
# Edit 锚点追加 → 畸形行 + 900s 超时 → 会话重置级联。12000 字符即使按
# CJK 1 token/字符也远低于限制，且保留首尾（尾部是 Edit 追加的锚点）。
_LEDGER_STR_CAP = 12000

# 整行序列化后的预算：多字段记录（如一条 tool_question 带多个 question/
# option）按字段截断后加起来仍可能超限，此时按比例收紧字段上限重截。
_LEDGER_LINE_CAP = 48000


def _cap_ledger_text(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    half = cap // 2
    omitted = len(text) - 2 * half
    return (
        text[:half]
        + f"\n... [middle truncated, {omitted} chars omitted] ...\n"
        + text[-half:]
    )


def _cap_ledger_value(value: Any, cap: int) -> Any:
    if isinstance(value, str):
        return _cap_ledger_text(value, cap)
    if isinstance(value, dict):
        return {key: _cap_ledger_value(item, cap) for key, item in value.items()}
    if isinstance(value, list):
        return [_cap_ledger_value(item, cap) for item in value]
    return value


def _cap_ledger_record(record: dict[str, Any]) -> dict[str, Any]:
    cap = _LEDGER_STR_CAP
    while True:
        capped = _cap_ledger_value(record, cap)
        if (
            len(json.dumps(capped, ensure_ascii=False)) <= _LEDGER_LINE_CAP
            or cap <= 750
        ):
            return capped
        cap //= 2


# AskUserQuestion 带外应答的提示词（2026-09-15）。全英文约定与剧本/simulator
# prompt 一致；强调"不是用户轮次"，防止模拟器把它计入剧本进度或趁机释放
# 信息片/探针。
_TOOL_QUESTION_PROMPT = """\
[Out-of-band tool question — NOT a user turn]

The agent has just called a UI question tool (AskUserQuestion) mid-turn. The \
hook intercepted it and routed the question(s) to you. This exchange is \
outside the normal user-message flow: it does NOT count as a turn, does NOT \
advance the script, and no simulator_turn record is written for it.

Rules for your reply:
- Answer in character as the user, briefly and directly, based on the \
persona, the script, and the conversation so far.
- Answer every question. If a question offers options, pick one (or say you \
have no preference, if that is what this user would say).
- Do NOT release any info-slice or probe content unless the question \
directly asks for something already due — when in doubt, withhold it.
- Do NOT mention this protocol, the script, or the fact that you are a \
simulator.
- If this user genuinely would not know the answer, say so the way the user \
would (e.g. "not sure — use your best judgment").

Question(s) from the agent:
{questions}

Reply with ONLY the answer text the user would give. It will be delivered \
back to the agent as the tool's response.\
"""


class ScriptedUserSimulator:
    """Thin message adapter around one persistent Agent Simulator session."""

    def __init__(
        self,
        *,
        config: Any,
        script_path: Path,
        original_instruction: str = "",
    ) -> None:
        self._config = config
        self._script_path = script_path
        self._original_instruction = original_instruction.strip()
        self._script: dict[str, Any] = json.loads(
            script_path.read_text(encoding="utf-8")
        )
        if self._script.get("turns"):
            raise ValueError(
                "script.json contains a legacy turns array; the Agent-based "
                "simulator only supports the "
                "opening/info_slices/probes/closing structure"
            )

        # 模拟器模型与被测模型解耦：harbor 脚本会把 ANTHROPIC_MODEL 导出为
        # **被测**模型，模拟器会话若继承环境会跟着 trial 模型跑，测量不可比。
        # 默认值可用 HARBOR_USER_SIM_AGENT_MODEL 覆盖。
        sim_env = dict(os.environ)
        sim_env["ANTHROPIC_MODEL"] = os.environ.get(
            "HARBOR_USER_SIM_AGENT_MODEL", "claude-sonnet-4-6"
        )
        # sim 专用端点覆盖（2026-09-20）：sim 要用自定义端点
        # （实测该类端点同时讲 Anthropic 协议 /v1/messages）时，不能改
        # 进程级 ANTHROPIC_BASE_URL——judge 子进程 env 继承它（judge/runner.py
        # 只覆盖 ANTHROPIC_MODEL），一改 judge 也跟着打到 sim 端点上。
        _sim_base = os.environ.get("HARBOR_USER_SIM_AGENT_BASE_URL", "").strip()
        if _sim_base:
            sim_env["ANTHROPIC_BASE_URL"] = _sim_base
        _sim_key = os.environ.get("HARBOR_USER_SIM_AGENT_API_KEY", "").strip()
        if _sim_key:
            sim_env["ANTHROPIC_API_KEY"] = _sim_key
            sim_env["ANTHROPIC_AUTH_TOKEN"] = _sim_key
        self._sim_model_name = sim_env["ANTHROPIC_MODEL"]
        self._session = ClaudeAgentSession(env=sim_env)
        self._progress_path = self._session.workdir / "simulator_progress.jsonl"
        self._outbox_path = self._session.workdir / "simulator_turn.json"
        self._handoff_error = ""
        self._simulator_script_path = self._session.workdir / "script.json"
        self._simulator_script_path.write_text(
            script_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self._guide_path = Path(__file__).with_name("AGENT_SIMULATOR.md")
        # 职责说明也拷入模拟器 workdir，首轮简报只给路径（2026-08-31：不内联
        # 全文）。任务说明（原始 instruction）不给模拟器——v0.4 覆盖不变式
        # 要求 info_slices ⊇ instruction，剧本即用户的全部知识；漏片应在
        # P1 validate/oracle_contract 暴露，而非模拟器兜底。
        self._simulator_guide_path = self._session.workdir / "AGENT_SIMULATOR.md"
        self._simulator_guide_path.write_text(
            self._guide_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self._brief_sent = False
        self._opening_sent = False
        self._closing_sent = False
        self._user_messages_sent = 0
        self._history: list[dict[str, str]] = []
        self._execution_log: list[dict[str, Any]] = []
        # 用户动作（C3）：模拟器在 simulator_turn 里声明 user_action_ids，
        # runner 在消息发给被测 agent 前真实执行（docker exec 进容器）。
        self._pending_user_actions: list[str] = []
        # AskUserQuestion 带外应答的串行锁（懒创建）：正常情况下 hook 请求只会在
        # 被测 agent 轮进行中到达（此时模拟器空闲），锁只是兜底。
        self._tool_question_lock: asyncio.Lock | None = None
        self._write_initial_progress()

    async def answer_tool_question(self, questions: list[Any]) -> str:
        """带外应答被测 agent 的 AskUserQuestion 工具调用（2026-09-15）。

        由 ask_user_rpc 的 PreToolUse hook 链路中轮触发。不计入剧本轮次、
        不推进进度、不释放信息片/探针；回答以 "tool_question" 形式同时写入
        execution_log（judge 可见）与 simulator_progress.jsonl。
        """
        if self._tool_question_lock is None:
            self._tool_question_lock = asyncio.Lock()
        lines: list[str] = []
        for idx, question in enumerate(questions, start=1):
            if not isinstance(question, dict):
                lines.append(f"{idx}. {question}")
                continue
            text = str(question.get("question", "")).strip()
            options = question.get("options") or []
            option_text = "; ".join(
                str(opt.get("label", opt)) if isinstance(opt, dict) else str(opt)
                for opt in options
            )
            multi = " (multi-select)" if question.get("multiSelect") else ""
            lines.append(
                f"{idx}. {text}"
                + (f"  [options: {option_text}]{multi}" if option_text else multi)
            )
        prompt = _TOOL_QUESTION_PROMPT.format(questions="\n".join(lines) or "(empty)")
        async with self._tool_question_lock:
            answer = (await self._session.call(prompt)).strip()
        self._execution_log.append(
            {
                "seq": len(self._execution_log) + 1,
                "kind": "tool_question",
                "questions": questions,
                "answer": answer,
                "reason": "AskUserQuestion hook relay (out-of-band, no turn consumed)",
            }
        )
        self._append_jsonl(
            {
                "type": "tool_question",
                "turn": self._user_messages_sent,
                "questions": questions,
                "answer": answer,
            }
        )
        return answer

    def receive_agent_message(self, content: str) -> None:
        self._history.append({"role": "assistant", "content": content})
        self._write_latest_agent_reply(content)

    def should_stop(self, user_message: str) -> bool:
        # 有内容的closing必须先交给agent；正文提及停止标记不等于协议stop。
        return user_message.strip() in self._stop_phrases()

    def history(self) -> list[dict[str, str]]:
        return [dict(item) for item in self._history]

    def execution_log(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._execution_log]

    def _unfired_probe_ids(self) -> list[str]:
        probes = self._script.get("probes") or {}
        required = {
            pid
            for pid, spec in probes.items()
            if isinstance(spec, dict) and requires_delivery(spec)
        }
        fired = delivered_probe_ids(self._script, self._execution_log)
        return sorted(required - fired - set(self._degraded_probe_ids()))

    @staticmethod
    def _probe_stimulus_anchor(stimulus: str) -> str:
        return stimulus_anchor(stimulus)

    def _degraded_probe_ids(self) -> list[str]:
        """锚点校验连续 ≥2 次未过的探针：simulator 已尽力重发，继续拦关单
        只是空耗轮次和 token（2026-09-16 jws codex 实测 8 轮拉锯）。降级：
        不再拦关单，记账供 judge 参考。"""
        counts: dict[str, int] = {}
        for e in self._execution_log:
            for p in e.get("probe_unverified") or []:
                counts[p] = counts.get(p, 0) + 1
        explicit = {
            pid
            for e in self._execution_log
            for pid, reason in (e.get("probe_degradations") or {}).items()
            if pid in (self._script.get("probes") or {})
            and len(str(reason).strip()) >= 20
        }
        return sorted({p for p, n in counts.items() if n >= 2} | explicit)

    def _detect_fired_probes(self, message: str, reported: list[str]) -> list[str]:
        probes = self._script.get("probes") or {}
        return [
            pid
            for pid in self._unfired_probe_ids()
            if pid not in reported and message_matches_probe(probes[pid], message)
        ]

    def _verify_probe_ids(
        self, message: str, probe_ids: list[str]
    ) -> tuple[list[str], list[str]]:
        probes = self._script.get("probes") or {}
        verified: list[str] = []
        unverified: list[str] = []
        for pid in probe_ids:
            spec = probes.get(pid)
            if not isinstance(spec, dict):
                unverified.append(pid)
            elif (
                not requires_delivery(spec)
                or not stimulus_anchor(str(spec.get("stimulus") or ""))
                or message_matches_probe(spec, message, allow_response=True)
            ):
                verified.append(pid)
            else:
                unverified.append(pid)
        return verified, unverified

    def _unreleased_slice_ids(self) -> list[str]:
        return unreleased_slice_ids(self._script, self._execution_log)

    def unfired_turn_ids(self) -> list[str]:
        return self._unfired_probe_ids()

    def progress_snapshot(self) -> str:
        try:
            return self._progress_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    async def _call_simulator(self, prompt: str) -> str:
        """模型只能提交下一条决定，不能改写runner已经记录的历史。"""
        before = (
            self._progress_path.read_bytes() if self._progress_path.exists() else b""
        )
        try:
            return await self._session.call(prompt)
        finally:
            after = (
                self._progress_path.read_bytes()
                if self._progress_path.exists()
                else b""
            )
            valid_append = after.startswith(before)
            if valid_append:
                try:
                    for line in after[len(before) :].splitlines():
                        if line.strip():
                            record = json.loads(line)
                            if (
                                not isinstance(record, dict)
                                or record.get("type") != "simulator_turn"
                            ):
                                valid_append = False
                except (ValueError, UnicodeDecodeError):
                    valid_append = False
            if not valid_append:
                self._progress_path.write_bytes(before)
                logger.warning(
                    "simulator attempted to rewrite/corrupt the ledger; restored runner history"
                )

    async def next_user_message(self) -> str:
        if self._closing_sent:
            self._write_stop_message()
            return self._record_message(
                self._stop_phrase(), phase="stop", reason="closing already sent"
            )

        expected_turn = self._user_messages_sent + 1
        budget_left = self._config.max_turns - self._user_messages_sent
        prompt = self._build_prompt(budget_left, expected_turn=expected_turn)
        decision = None
        problem = ""
        closing_retries = 0
        handoff_failures = 0
        # 双预算（2026-09-19 拍板）：
        # - 交接/格式失败（多为 API 故障）：最多 3 次（1 首发 + 2 纠错）——
        #   端点挂死型零响应重试再多也没用，快速熔断标记 simulator_broken；
        # - 关单清算打回（sim 活着在干活）：沿用 5 次上限（2026-09-16 拍板），
        #   给 simulator 足够机会补放 slice/探针，最后 degraded 放行记账。
        # 循环上限 8 = 3 + 5，两类各自计数；退避只作用于交接失败。
        for attempt in range(8):
            records_before = len(self._read_jsonl())
            if attempt == 0:
                await self._call_simulator(prompt)
            elif problem and problem.startswith("BLOCKED:"):
                # closing 硬拦截打回（runner 侧硬规则）：探针未发完或必达
                # slice 未放完，且预算未耗尽 → 让模拟器继续。判据是 runner
                # 自己记的 execution_log，不信模拟器自报的账本。
                # （2026-09-01 起只拦探针；2026-09-16 起加拦 slice。）
                await self._call_simulator(
                    f"Your closing was REJECTED: {problem[len('BLOCKED:') :]}. "
                    f"Remaining user-message budget: {budget_left}. Continue "
                    "the conversation instead — fire the pending probes at "
                    "natural openings, and deliver the remaining info slices' "
                    "content (bundle the last few naturally if several are "
                    "left). You MUST write one NEW decision to simulator_turn.json for "
                    f"turn {expected_turn} (phase 'dialogue') — replying in chat without "
                    "writing the file does NOT unblock the closing."
                )
            else:
                # 交接格式纠错：先退避（端点抖动窗口内背靠背重试纯属陪绑，
                # 2026-09-18 实测），再告诉模拟器上一条记录错在哪
                handoff_failures += 1
                if handoff_failures > 2:
                    break  # 交接失败预算耗尽 → 下方 simulator_broken 降级
                # 格式纠错不需要等待一分钟；网络重试由agent session负责。
                backoff = float(
                    os.environ.get("HARBOR_USER_SIM_HANDOFF_BACKOFF_SEC", "2")
                )
                await asyncio.sleep(max(0, backoff) * handoff_failures)
                await self._call_simulator(
                    f"Handoff validation failed: {problem}\n\n"
                    f"Read the diagnosis and rewrite one valid "
                    f"decision in simulator_turn.json (turn must equal "
                    f"{expected_turn}, message must be non-empty; never modify "
                    "the append-only ledger). A chat reply without writing the outbox "
                    "does not count."
                )
            decision = self._read_file_decision(expected_turn=expected_turn)
            if decision is not None and len(self._read_jsonl()) <= records_before:
                # 同一条记录被复读（模拟器只回话没写新记录）：不能用旧决定
                # 复读 5 次把打回预算烧完（2026-09-18 verl 实测：打回提示语
                # 结尾的 "Reply with a short confirmation only" 让模拟器只回
                # 话不写文件，同一条 closing 被复读 5 次后降级放行，13 个
                # 信息片与 5 个探针永远没发）。
                decision = None
                problem = (
                    f"No NEW simulator_turn record for turn {expected_turn} "
                    "was appended to simulator_progress.jsonl — a chat reply "
                    "does not count as a decision."
                )
            if decision is not None:
                if decision["phase"] == "closing":
                    verified, _ = self._verify_probe_ids(
                        decision["message"], decision["probe_ids"]
                    )
                    candidate = {**decision, "probe_ids": verified}
                    unfired = sorted(
                        set(self._unfired_probe_ids())
                        - set(verified)
                        - set(decision.get("probe_degradations") or {})
                    )
                    unreleased = unreleased_slice_ids(
                        self._script, [*self._execution_log, candidate]
                    )
                    blocked_parts = []
                    if unfired:
                        blocked_parts.append("probes not fired: " + ", ".join(unfired))
                    if unreleased:
                        blocked_parts.append(
                            "info slices not released: " + ", ".join(unreleased)
                        )
                    if blocked_parts and budget_left > 2 and closing_retries < 5:
                        closing_retries += 1
                        problem = "BLOCKED:" + "; ".join(blocked_parts)
                        decision = None
                        continue
                    if blocked_parts:
                        decision["reason"] = (
                            (decision.get("reason") or "")
                            + " | closing with unfired probes / unreleased "
                            f"slices (degraded): {'; '.join(blocked_parts)}"
                        ).strip(" |")
                break
            problem = self._diagnose_handoff(expected_turn)

        if decision is None:
            # 优雅降级（2026-08-31 起）：连续多次交接失败多为链路/API 故障，
            # 提前以 stop 收尾并标记 simulator_broken（judge/归因按 infra 事故
            # 处理），不再用 RuntimeError 拖死整个 trial。
            logger.warning(
                "user-sim handoff failed 3 times at turn %d: %s",
                expected_turn,
                problem,
            )
            self._closing_sent = True
            return self._record_message(
                self._stop_phrase(),
                phase="closing",
                reason=f"simulator_broken: no valid handoff for turn "
                f"{expected_turn} after 3 attempts ({problem})",
            )

        self._pending_user_actions = decision["user_action_ids"]
        phase = decision["phase"]
        self._opening_sent = self._opening_sent or phase == "opening"
        self._closing_sent = self._closing_sent or phase == "closing"
        # 探针锚点回填：自报 probe_ids 漏写但 stimulus 锚点逐字在消息里的，
        # 补记为已发（见 _detect_fired_probes 注释）。在 _record_message 之前
        # 做，保证 execution_log 与 closing 清算的口径一致。
        detected = self._detect_fired_probes(decision["message"], decision["probe_ids"])
        if detected:
            decision["probe_ids"] = list(decision["probe_ids"]) + detected
            decision["reason"] = (
                (decision.get("reason") or "")
                + f" | runner anchor-detected probes: {', '.join(detected)}"
            ).strip(" |")
        message = decision["message"]
        if phase == "closing":
            for phrase in self._stop_phrases():
                message = message.replace(phrase, "").strip()
            message = message or decision["message"]
        return self._record_message(
            message,
            phase=phase,
            probe_ids=decision["probe_ids"],
            released_slice_ids=decision["released_slice_ids"],
            reason=decision["reason"],
            probe_degradations=decision.get("probe_degradations"),
        )

    def _build_prompt(self, budget_left: int, expected_turn: int) -> str:
        parts: list[str] = [
            f"Authoritative guide: {self._simulator_guide_path}. Script: {self._simulator_script_path}. "
            "If this is a fresh/recovered session, read both before acting. The ledger is read-only.\n"
        ]
        if not self._brief_sent:
            parts.extend(
                [
                    "[First turn] Read these two files in your workdir before "
                    "doing anything:\n",
                    f"1. {self._simulator_guide_path} — your role guide "
                    "(role, per-turn procedure, decision rules, ledger format)\n",
                    f"2. {self._simulator_script_path} — the complete script.json "
                    "for this task\n\n",
                    "Read both files in full first. After that, rely on this "
                    "persistent session and simulator_progress.jsonl; you do NOT "
                    "need to re-read the script every turn, but re-Read it "
                    "whenever your memory is uncertain.\n",
                    f"Ledger: {self._progress_path}\n",
                    "\n\n",
                ]
            )
            self._brief_sent = True
        # 被测 Agent 最新回复内联进 prompt（省一次 Read；账本里的
        # target_agent_reply 记录同样是截断版，文件仍是唯一交接边界）
        latest_reply = self._history[-1]["content"] if self._history else ""
        if len(latest_reply) > 8000:
            latest_reply = (
                latest_reply[:4000] + "\n... (middle truncated for size; the "
                "target_agent_reply record in simulator_progress.jsonl is "
                "capped the same way) ...\n" + latest_reply[-4000:]
            )
        parts.extend(
            [
                "[This turn]\n",
                f"This is user message #{expected_turn} — the simulator_turn "
                f"outbox you write must have turn == "
                f"{expected_turn}.\n",
                f"Remaining user-message budget: {budget_left}\n",
            ]
        )
        # 每轮亮出 runner 侧账本里还欠的片/探针（不信模拟器自报）。放不完的
        # closing 会被打回，不如每轮提醒"你的任务还包括把这些全送出去"
        # （2026-09-18：opus-cc sim_not_ok 22% 的主因就是片欠着就收尾）。
        unreleased = self._unreleased_slice_ids()
        unfired = self._unfired_probe_ids()
        if unreleased or unfired:
            backlog: list[str] = []
            if unreleased:
                backlog.append(
                    f"{len(unreleased)} info slices: " + ", ".join(unreleased)
                )
            if unfired:
                backlog.append(f"{len(unfired)} probes: " + ", ".join(unfired))
            parts.append(
                "Still to deliver before any closing is accepted (runner-side "
                "ledger, not your own): " + " | ".join(backlog) + "\n"
            )
        cancelled = sorted(cancelled_slice_ids(self._script, self._execution_log))
        passive = [
            pid
            for pid, p in (self._script.get("probes") or {}).items()
            if isinstance(p, dict) and not requires_delivery(p)
        ]
        if cancelled or passive:
            parts.append(
                f"Protocol-only state (never quote to the user): cancelled slices={cancelled}; passive probes={passive}. Do not deliver these just to close.\n"
            )
        if latest_reply:
            parts.extend(
                [
                    "The target agent's latest reply (full text inline below; "
                    "no need to Read the ledger for it):\n",
                    "<target_agent_reply>\n",
                    latest_reply,
                    "\n</target_agent_reply>\n\n",
                ]
            )
        parts.append(
            "Now decide the next action based on the first-turn handoff, the "
            "script, and current progress; then write exactly one JSON object "
            "to simulator_turn.json with turn, message, phase, probe_ids and released_slice_ids. "
            "The runner owns the append-only ledger: do not edit it. "
            "For a probe whose prerequisite never arose, provide probe_degradations as a mapping "
            "from probe id to a concrete reason; do not invent work or force a repeat. "
            "If you fire probe(s) this turn, quote each probe's stimulus core "
            "sentences verbatim in the message (framing may be natural, the "
            "scripted wording must not change). "
            "Reply with a short confirmation only."
        )
        return "".join(parts)

    def pop_pending_user_actions(self) -> list[str]:
        """取走本轮声明的用户动作 id 列表（runner 调用一次后清空）。"""
        ids = self._pending_user_actions
        self._pending_user_actions = []
        return ids

    def user_action_cmd(self, action_id: str) -> str | None:
        """按 id 查剧本 user_actions 里的 cmd；不存在返回 None。"""
        for action in self._script.get("user_actions") or []:
            if isinstance(action, dict) and action.get("id") == action_id:
                command = action.get("cmd")
                return command if isinstance(command, str) else None
        return None

    def record_user_action_results(self, results: list[dict[str, Any]]) -> None:
        """runner 执行完用户动作后回写：账本 action_result 记录（模拟器下轮可见）
        + execution_log（judge 证据）。"""
        for r in results:
            self._append_jsonl({"type": "action_result", **r})
        if self._execution_log:
            self._execution_log[-1]["user_actions"] = results

    def _diagnose_handoff(self, expected_turn: int) -> str:
        if getattr(self, "_handoff_error", ""):
            return self._handoff_error
        """诊断交接失败原因，供定向纠错 prompt 使用。"""
        records = self._read_jsonl()
        last_turn = next(
            (r for r in reversed(records) if r.get("type") == "simulator_turn"),
            None,
        )
        if last_turn is None:
            return (
                f"no simulator_turn record in the ledger (this turn must be "
                f"turn={expected_turn})"
            )
        turn = last_turn.get("turn")
        state = last_turn.get("state")
        if not last_turn.get("message"):
            return (
                f"the last simulator_turn (turn={turn}) has an empty "
                f"message; this turn must be turn={expected_turn}"
            )
        if not isinstance(state, dict):
            return (
                f"the last simulator_turn (turn={turn}) is missing the "
                f"state field; this turn must be turn={expected_turn}"
            )
        if turn != expected_turn or state.get("turn") != expected_turn:
            return (
                f"the last simulator_turn has turn={turn}, "
                f"state.turn={state.get('turn')}; this turn must be "
                f"{expected_turn}"
            )
        return (
            f"the last simulator_turn record is invalid; this turn must "
            f"be turn={expected_turn}"
        )

    def _record_message(
        self,
        message: str,
        *,
        phase: str,
        probe_ids: list[str] | None = None,
        released_slice_ids: list[str] | None = None,
        reason: str = "",
        probe_degradations: dict[str, str] | None = None,
    ) -> str:
        message = message.strip()
        self._user_messages_sent += 1
        self._history.append({"role": "user", "content": message})
        # 校验自报 fired 的探针是否真发了 stimulus 文本（probe_unverified 的
        # 探针在 _unfired_probe_ids 里不算已发，关单清算会接着拦）
        probe_unverified: list[str] = []
        if probe_ids:
            _, probe_unverified = self._verify_probe_ids(message, probe_ids)
        entry: dict[str, Any] = {
            "seq": len(self._execution_log) + 1,
            "kind": phase,
            "message": message,
            "probe_ids": probe_ids or [],
            "released_slice_ids": released_slice_ids or [],
            "probes_pending_after": self._unfired_probe_ids(),
            "reason": reason,
            "probe_degradations": probe_degradations or {},
        }
        if probe_unverified:
            entry["probe_unverified"] = probe_unverified
        # 关单时把降级探针（锚点校验 ≥2 次未过、已放行）写进账本供 judge 参考
        if phase == "closing":
            degraded = self._degraded_probe_ids()
            if degraded:
                entry["degraded_probe_ids"] = degraded
        self._execution_log.append(entry)
        entry["probes_pending_after"] = self._unfired_probe_ids()
        entry["cancelled_slice_ids"] = sorted(
            cancelled_slice_ids(self._script, self._execution_log)
        )
        state = dict(self._read_progress_state())
        state.update(
            turn=self._user_messages_sent,
            phase=phase,
            completed_probe_ids=sorted(
                delivered_probe_ids(self._script, self._execution_log)
            ),
            pending_probe_ids=entry["probes_pending_after"],
            degraded_probe_ids=self._degraded_probe_ids(),
            released_slice_ids=sorted(
                {
                    sid
                    for e in self._execution_log
                    for sid in e.get("released_slice_ids") or []
                }
            ),
            cancelled_slice_ids=entry["cancelled_slice_ids"],
        )
        self._append_jsonl(
            {
                "type": "message_sent",
                "turn": self._user_messages_sent,
                "message": message,
                "phase": phase,
                "reason": reason,
                "state": state,
            }
        )
        return message

    def _write_initial_progress(self) -> None:
        probes = [
            key
            for key, value in (self._script.get("probes") or {}).items()
            if isinstance(value, dict) and requires_delivery(value)
        ]
        state = {
            "phase": "opening",
            "turn": 0,
            "completed_probe_ids": [],
            "pending_probe_ids": sorted(probes),
            "released_slice_ids": [],
            "degraded_probe_ids": [],
            "last_action": "",
            "evidence": "",
            "blocker": "",
            "next_action": "send opening",
            "closing_allowed": False,
        }
        self._progress_path.write_text("", encoding="utf-8")
        self._append_jsonl(
            {
                "type": "init",
                "turn": 0,
                "script_path": str(self._simulator_script_path),
                # 实际生效的 sim 模型落账（2026-09-19 根因教训：模型名经过
                # 多层默认覆盖时必须可审计，排查先看账本再看 transcript）
                "sim_model": self._sim_model_name,
                "state": state,
            }
        )

    def _write_latest_agent_reply(self, content: str) -> None:
        """Make the target-agent reply inspectable in the simulator workdir.

        The content is capped by _append_jsonl (head+tail kept) so every
        ledger line stays readable by the simulator's Read tool.
        """
        self._append_jsonl(
            {
                "type": "target_agent_reply",
                "for_turn": self._user_messages_sent + 1,
                "content": content,
            }
        )

    def _write_stop_message(self) -> None:
        """Record the protocol stop message in the same handoff file."""
        state = self._read_progress_state()
        turn = int(state.get("turn", self._user_messages_sent)) + 1
        state.update(
            {
                "turn": turn,
                "phase": "finished",
                "last_action": "send stop phrase",
                "next_action": "finish",
                "closing_allowed": True,
            }
        )
        self._append_jsonl(
            {
                "type": "simulator_turn",
                "turn": turn,
                "message": self._stop_phrase(),
                "phase": "stop",
                "probe_ids": [],
                "released_slice_ids": [],
                "reason": "closing already sent",
                "state": state,
            }
        )

    def _append_jsonl(self, record: dict[str, Any]) -> None:
        # 所有 runner 写入的记录统一过字符串截断，保证每行都在模拟器 Read
        # 工具的硬限制之内（模拟器自己写的 simulator_turn 不经过这里）。
        record = _cap_ledger_record(record)
        with self._progress_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _read_file_decision(self, *, expected_turn: int) -> dict[str, Any] | None:
        """Read the simulator's completed turn from its progress file.

        The file is the handoff boundary. The model's stdout is deliberately
        ignored so that progress and the message sent to the target agent
        cannot silently diverge.
        """
        self._handoff_error = ""
        outbox = getattr(
            self, "_outbox_path", self._progress_path.with_name("simulator_turn.json")
        )
        if outbox.exists():
            try:
                raw = json.loads(outbox.read_text(encoding="utf-8"))
                if not isinstance(raw, dict) or raw.get("turn") != expected_turn:
                    raise ValueError(f"outbox turn must equal {expected_turn}")
                decision = self._normalize_decision(raw)
                if decision is None:
                    raise ValueError("message/phase/id arrays are invalid")
                self._validate_decision_ids(decision)
            except (OSError, ValueError, TypeError) as exc:
                self._handoff_error = f"simulator_turn.json: {exc}"
                return None
            state = dict(self._read_progress_state())
            state.update(turn=expected_turn, phase=decision["phase"])
            self._append_jsonl(
                {
                    "type": "simulator_turn",
                    "turn": expected_turn,
                    **decision,
                    "state": state,
                }
            )
            outbox.unlink()
            return decision
        data = next(
            (
                record
                for record in reversed(self._read_jsonl())
                if record.get("type") == "simulator_turn"
            ),
            None,
        )
        if not isinstance(data, dict):
            return None
        try:
            turn = int(data.get("turn", -1))
        except (TypeError, ValueError):
            return None
        if turn != expected_turn:
            return None
        state = data.get("state")
        if not isinstance(state, dict):
            return None
        try:
            state_turn = int(state.get("turn", -1))
        except (TypeError, ValueError):
            return None
        if state_turn != expected_turn:
            return None
        decision = self._normalize_decision(data)
        if decision is not None:
            try:
                self._validate_decision_ids(decision)
            except ValueError as exc:
                self._handoff_error = str(exc)
                return None
        return decision

    def _validate_decision_ids(self, decision: dict[str, Any]) -> None:
        if (
            decision["phase"] == "stop"
            or decision["message"].strip() in self._stop_phrases()
        ):
            raise ValueError(
                "send a normal closing request; the runner emits the standalone stop marker afterwards"
            )
        probes = set(self._script.get("probes") or {})
        slices = {s["id"] for s in self._script.get("info_slices") or []}
        if (
            set(decision["probe_ids"]) - probes
            or set(decision["released_slice_ids"]) - slices
        ):
            raise ValueError("decision references unknown probe or slice ids")
        reasons = decision.get("probe_degradations") or {}
        if set(reasons) - probes or any(
            len(str(r).strip()) < 20 for r in reasons.values()
        ):
            raise ValueError(
                "probe_degradations requires known ids and concrete reasons"
            )
        if re.search(
            r"all (?:six|[0-9]+) probes (?:are |have been )?(?:now )?fired|slice[- ]delivery mode|simulator_progress\.jsonl|probe_unverified|degraded_probe_ids",
            decision["message"],
            re.IGNORECASE,
        ):
            raise ValueError(
                "message contains simulator protocol internals; rewrite naturally"
            )

    def _read_progress_state(self) -> dict[str, Any]:
        for record in reversed(self._read_jsonl()):
            state = record.get("state")
            if record.get("type") in {
                "simulator_turn",
                "message_sent",
                "init",
            } and isinstance(state, dict):
                return state
        return {}

    def _read_jsonl(self) -> list[dict[str, Any]]:
        try:
            lines = self._progress_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        records: list[dict[str, Any]] = []
        for line in lines:
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if isinstance(record, dict):
                records.append(record)
        return records

    def _stop_phrases(self) -> list[str]:
        return [p.strip() for p in self._config.stop_phrases if p.strip()]

    def _stop_phrase(self) -> str:
        return (self._stop_phrases() or ["#### STOP"])[0]

    @staticmethod
    def _normalize_decision(data: dict[str, Any]) -> dict[str, Any] | None:
        try:
            message = str(data.get("message") or "").strip()
            phase = str(data.get("phase") or "dialogue")
            for key in ("probe_ids", "released_slice_ids", "user_action_ids"):
                value = data.get(key) or []
                if not isinstance(value, list) or not all(
                    isinstance(v, str) for v in value
                ):
                    return None
            probe_ids = list(dict.fromkeys(data.get("probe_ids") or []))
            released_slice_ids = list(data.get("released_slice_ids") or [])
            user_action_ids = [str(a) for a in (data.get("user_action_ids") or [])]
        except (TypeError, ValueError):
            return None
        if not message:
            return None
        if phase not in {"opening", "dialogue", "closing", "stop"}:
            return None
        degradations = data.get("probe_degradations") or {}
        if not isinstance(degradations, dict):
            return None
        return {
            "message": message,
            "phase": phase,
            "probe_ids": probe_ids,
            "released_slice_ids": released_slice_ids,
            "user_action_ids": user_action_ids,
            "probe_degradations": degradations,
            "reason": str(data.get("reason") or ""),
        }
