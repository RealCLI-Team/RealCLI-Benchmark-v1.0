"""harbor.judge.runner — 对一次交互评测 trial 打分（v0.3 checklist 制）。

移植自 clibench ``judge/run_judge.py``（harbor 为单一事实来源）。判定
纪律只改 ``prompt.md``，本文件不含措辞。

输入：instance/task 目录（读 interaction/script.json + 向上查找的
rubrics.json / domains.md+dim_selection.json）与 trial 产物目录（agent/
输出、interaction_evidence/ 证据包、result.json 的 user_simulation 账本）。

judge backend：
- claude_cli：以 claude --print agentic 模式运行，工作目录为 trial 目录；
- codex_cli：以 codex exec 非交互模式运行，工作目录为 trial 目录。

judge 自行阅读 transcript 与证据包，完成两部分评估：
1. agent 交互维度：对每个维度/场景的 checklist 逐项判定（0/1/n/a）并引用
   证据。judge 只判项；维度分（x/y = 通过项/适用项）由本模块在 judge
   输出后计算并回填。
2. 用户模拟器评估：审查模拟器是否按剧本执行，标出可能被模拟器失误污染
   的维度（contaminated_dimensions），供下游聚合时剔除。

与旧版差异（已知 bug 修复）：
- 维度键嵌套/场景不齐等校验失败改为抛 JudgeError（清晰报错），不再
  sys.exit 导致后处理整段跳过；
- judge backend 非零退出码向上传递（JudgeBackendError.returncode），
  CLI main 以该 rc 退出，不吞。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

from harbor.judge.models import JudgeResult
from harbor.judge.scenarios import (
    SCENARIO_ID_RE,
    find_upwards,
    load_domains,
    load_selection,
    scenario_index,
)
from harbor.trial.scripted_user_simulator import ScriptedUserSimulator
from harbor.trial.probe_protocol import (
    cancelled_slice_ids,
    delivered_probe_ids,
    requires_delivery,
)
from harbor.trial.sim_agent_backend import _CLAUDE_BIN_CANDIDATES

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE_PATH = Path(__file__).with_name("prompt.md")
DIMENSION_SCORES_FILENAME = "dimension_scores.json"
JUDGE_PROMPT_FILENAME = "judge_prompt.md"
DEFAULT_JUDGE_MODEL = "claude-sonnet-4-6"
DEFAULT_TIMEOUT_SEC = 3600.0
DEFAULT_MAX_OUTPUT_TOKENS = 64000

SIM_CHECK_IDS = {
    "sim_intent_fidelity",
    "sim_no_info_leak",
    "sim_answer_fidelity",
    "sim_no_state_assertion",
    "sim_probe_coverage",
    "sim_no_empty_or_leaked_message",
    "sim_no_refusal",
    "sim_timing_natural",
    "sim_slice_coverage",
}


class JudgeError(RuntimeError):
    """judge 输入/输出校验失败（清晰报错，替代旧版 sys.exit 致命退出）。"""


class JudgeBackendError(JudgeError):
    """judge backend 子进程失败；returncode 向上传递不吞。"""

    def __init__(self, backend: str, returncode: int) -> None:
        self.backend = backend
        self.returncode = returncode
        super().__init__(f"judge backend {backend} exited with {returncode}")


@dataclass
class JudgeRuntimeConfig:
    """一次 judge 调用的运行时参数（task.toml [judge] + env 解析后的终态）。"""

    backend: str = "claude_cli"
    model_name: str = DEFAULT_JUDGE_MODEL
    timeout_sec: float = DEFAULT_TIMEOUT_SEC
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    claude_args: str = ""
    codex_args: str = ""
    claude_bin: str | None = None
    config_dir: str | None = None


# 测试可注入的假 backend；签名为 (prompt, trial_dir, config, env)。
BackendRunner = Callable[
    [str, Path, JudgeRuntimeConfig, dict[str, str]], Awaitable[None]
]


def _load_prompt_template() -> Template:
    if not PROMPT_TEMPLATE_PATH.is_file():
        raise JudgeError(f"judge 任务说明模板缺失: {PROMPT_TEMPLATE_PATH}")
    return Template(PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8"))


def _probe_stimulus_anchor(stimulus: str) -> str:
    """复用 runner 侧同款实现（保持两侧判定口径单一实现）。"""
    return ScriptedUserSimulator._probe_stimulus_anchor(stimulus)


def _fired_probe_ids(elog: list[dict[str, Any]], probes: dict[str, Any]) -> set[str]:
    # degraded means missing/partial evidence, not that the stimulus was delivered.
    return delivered_probe_ids({"probes": probes}, elog)


def _load_execution_log(trial_dir: Path) -> list[dict[str, Any]]:
    """runner 侧 sim 账本（execution_log）。优先 result.json 的
    user_simulation metadata，缺失时兜底 agent/user_sim_execution_log.json
    增量快照（trial 异常死亡时 result.json 可能整段缺失）。"""
    result_path = trial_dir / "result.json"
    if result_path.is_file():
        try:
            trial_result = json.loads(result_path.read_text(encoding="utf-8"))
            elog = (
                ((trial_result.get("agent_result") or {}).get("metadata") or {}).get(
                    "user_simulation"
                )
                or {}
            ).get("execution_log") or []
            if elog:
                return elog
        except (ValueError, OSError):
            pass
    snap_path = trial_dir / "agent" / "user_sim_execution_log.json"
    if snap_path.is_file():
        try:
            snap = json.loads(snap_path.read_text(encoding="utf-8"))
            return snap.get("execution_log") or []
        except (ValueError, OSError):
            return []
    return []


def _slice_fact_tokens(content: str) -> list[str]:
    """slice 内容的可判别事实 token：路径 / flag / 数字 / 长词（sim 按
    persona 规则改写措辞后逐字匹配必然全 miss，改用事实 token 重合度）。"""
    c = " ".join(str(content).split()).lower()
    toks = set()
    toks |= set(re.findall(r"/[\w./-]{3,}", c))
    toks |= set(re.findall(r"--[\w-]{2,}", c))
    toks |= set(re.findall(r"\b\d+(?:\.\d+)?\b", c))
    toks |= {w for w in re.findall(r"[a-z][a-z_]{5,}", c)}
    stop = {
        "should",
        "would",
        "could",
        "please",
        "thanks",
        "before",
        "after",
        "because",
        "through",
        "between",
        "without",
        "within",
        "really",
        "think",
        "that",
        "this",
        "with",
        "from",
        "your",
        "youre",
    }
    return sorted(toks - stop)


def _slice_content_hits(
    elog: list[dict[str, Any]], slices: list[dict[str, Any]]
) -> list[str]:
    """事实 token 重合度 ≥60% 判定 slice 内容已在 sim 消息里交付。"""
    msgs = [" ".join(str(e.get("message") or "").split()).lower() for e in elog]
    joined = " ".join(msgs)
    hits: list[str] = []
    for s in slices:
        toks = _slice_fact_tokens(s.get("content") or "")
        if not toks:
            continue
        if sum(1 for t in toks if t in joined) / len(toks) >= 0.6:
            hits.append(s["id"])
    return hits


def _slice_delivery_note(elog: list[dict[str, Any]], script: dict[str, Any]) -> str:
    """信息片释放统计，作为 judge 评估 sim_slice_coverage 的事实输入。
    给两个口径：runner 记账（released_slice_ids，可能漏记）+ 事实 token
    重合度估计（改写措辞也能识别）。最终认定由 judge 对照消息核实。"""
    cancelled = cancelled_slice_ids(script, elog)
    slices = [
        s
        for s in (script.get("info_slices") or [])
        if isinstance(s, dict)
        and isinstance(s.get("id"), str)
        and s["id"] not in cancelled
    ]
    if not slices:
        return "本题剧本无 info_slices（旧版维度体系），sim_slice_coverage 判 n/a。"
    total = {s["id"] for s in slices}
    released = {sid for e in elog for sid in (e.get("released_slice_ids") or [])}
    unverified = {sid for e in elog for sid in (e.get("release_unverified") or [])}
    effective = released - unverified
    content_hits = set(_slice_content_hits(elog, slices))
    never = sorted(total - effective - content_hits)
    return (
        f"信息片交付的两种口径：runner 记账释放 {len(effective)}/{len(total)}"
        f"（released_slice_ids 可能漏记）；按内容事实 token 重合度估计实际"
        f"交付 {len(content_hits)}/{len(total)}（命中的片: "
        f"{sorted(content_hits) or '无'}）。"
        f"两种口径下都未见交付的片: {never or '无'}。"
        f"已按剧本取消、不要求再交付的片: {sorted(cancelled) or '无'}。"
        "请以消息原文为最终依据核实。"
    )


def collect_checklist(
    dim: str, probe: dict[str, Any], rubrics: dict[str, Any]
) -> list[dict[str, Any]]:
    """汇总一个维度的 checklist：全局（被动/固定维度）+ 剧本定制项。"""
    items: list[dict[str, Any]] = []
    passive = rubrics.get("passive_dimensions", {}).get(dim, {})
    for it in passive.get("checklist", []):
        items.append({**it, "source": "global"})
    for it in probe.get("checklist", []):
        items.append({**it, "source": "script"})
    return items


def dimension_definition(dim: str, rubrics: dict[str, Any]) -> str:
    for section in ("passive_dimensions", "active_dimensions"):
        entry = rubrics.get(section, {}).get(dim)
        if entry and entry.get("definition"):
            return str(entry["definition"])
    return ""


def write_instruction_for_backend(backend: str, out_name: str) -> str:
    if backend == "claude_cli":
        return (
            f"Write the result JSON to **{out_name}** with the Write tool "
            "(the path may be absolute — write exactly there), not only in "
            "your final reply."
        )
    if backend == "codex_cli":
        return (
            f"Create or overwrite **{out_name}** (path may be absolute), JSON only;"
            "可以用 shell 命令写文件，但最终一定要把文件落盘，"
            "不要只在最终回复里贴 JSON。"
        )
    raise JudgeError(f"unsupported judge backend: {backend}")


def build_judge_env(
    *,
    model_name: str,
    max_output_tokens: int,
    config_dir: str | None = None,
    base: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build isolated judge CLI environment from HARBOR_JUDGE_* settings."""
    env = dict(os.environ if base is None else base)
    env["ANTHROPIC_MODEL"] = model_name
    env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(max_output_tokens)
    judge_base_url = env.get("HARBOR_JUDGE_BASE_URL", "").strip()
    if judge_base_url:
        env["ANTHROPIC_BASE_URL"] = judge_base_url
    judge_api_key = env.get("HARBOR_JUDGE_API_KEY", "").strip()
    if judge_api_key:
        env["ANTHROPIC_API_KEY"] = judge_api_key
        env["ANTHROPIC_AUTH_TOKEN"] = judge_api_key
    config_dir = config_dir or os.environ.get("HARBOR_JUDGE_CONFIG_DIR")
    if config_dir:
        env["CLAUDE_CONFIG_DIR"] = config_dir
    env["IS_SANDBOX"] = "1"
    return env


def _default_claude_bin() -> str:
    """解析链与 sim 侧（sim_agent_backend._default_claude_bin）一致，
    仅多一个 HARBOR_JUDGE_CLAUDE_BIN 优先覆盖。"""
    override = os.environ.get("HARBOR_JUDGE_CLAUDE_BIN") or os.environ.get(
        "HARBOR_USER_SIM_CLAUDE_BIN"
    )
    if override:
        return override
    for c in _CLAUDE_BIN_CANDIDATES:
        if Path(c).exists():
            return c
    found = shutil.which("claude")
    if found:
        return found
    raise JudgeError("未找到 claude CLI：请设置 HARBOR_JUDGE_CLAUDE_BIN 环境变量")


async def _run_subprocess(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_sec: float,
    input_text: str | None = None,
) -> int:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        env=env,
        stdin=asyncio.subprocess.PIPE if input_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(
            proc.communicate(input_text.encode("utf-8") if input_text else None),
            timeout=timeout_sec,
        )
    except (asyncio.TimeoutError, TimeoutError) as exc:
        proc.kill()
        await proc.wait()
        raise JudgeError(
            f"judge backend timed out after {timeout_sec} seconds"
        ) from exc
    if out:
        logger.debug(
            "judge backend output tail: %s",
            out.decode("utf-8", errors="replace")[-2000:],
        )
    return proc.returncode or 0


async def _run_claude_judge(
    prompt: str,
    trial_dir: Path,
    config: JudgeRuntimeConfig,
    env: dict[str, str],
) -> None:
    cmd = [
        config.claude_bin or _default_claude_bin(),
        "--print",
        prompt,
        "--allowedTools",
        "Read,Grep,Glob,Write,Bash",
    ]
    if config.claude_args:
        cmd.extend(shlex.split(config.claude_args))
    rc = await _run_subprocess(
        cmd, cwd=trial_dir, env=env, timeout_sec=config.timeout_sec
    )
    if rc != 0:
        raise JudgeBackendError("claude_cli", rc)


async def _run_codex_judge(
    prompt: str,
    trial_dir: Path,
    config: JudgeRuntimeConfig,
    env: dict[str, str],
) -> None:
    last_message = trial_dir / "codex_judge_last_message.txt"
    cmd = [
        "codex",
        "exec",
        "--cd",
        str(trial_dir),
        "--skip-git-repo-check",
        "--sandbox",
        "danger-full-access",
        "--output-last-message",
        str(last_message),
    ]
    if config.codex_args:
        cmd.extend(shlex.split(config.codex_args))
    cmd.append("-")
    rc = await _run_subprocess(
        cmd,
        cwd=trial_dir,
        env=env,
        timeout_sec=config.timeout_sec,
        input_text=prompt,
    )
    if rc != 0:
        raise JudgeBackendError("codex_cli", rc)


def _resolve_rubrics_path(instance_dir: Path) -> Path | None:
    override = os.environ.get("HARBOR_JUDGE_RUBRICS")
    if override:
        return Path(override)
    return find_upwards(instance_dir, "rubrics.json")


def _resolve_domains_paths(instance_dir: Path) -> tuple[Path, Path | None]:
    """v0.4 场景定义依赖 domains.md（必需）+ 选择集合（冻结快照
    domains/domain_v0.1.json 优先，其次页面实时选择 dim_selection.json，
    均可缺省 = 无 G 场景追加）。"""
    override_md = os.environ.get("HARBOR_JUDGE_DOMAINS_MD")
    domains_md = (
        Path(override_md) if override_md else find_upwards(instance_dir, "domains.md")
    )
    if domains_md is None or not domains_md.is_file():
        raise JudgeError(
            f"domains.md 未找到（从 {instance_dir} 向上查找均无；"
            "v0.4 剧本必需，可用 HARBOR_JUDGE_DOMAINS_MD 显式指定）"
        )
    override_sel = os.environ.get("HARBOR_JUDGE_DIM_SELECTION")
    if override_sel:
        return domains_md, Path(override_sel)
    root = domains_md.parent
    for cand in (root / "domains" / "domain_v0.1.json", root / "dim_selection.json"):
        if cand.is_file():
            return domains_md, cand
    return domains_md, None


def _unfired_attribution(trial_dir: Path, elog: list[dict[str, Any]]) -> str:
    """探针未发的归因：区分模拟器侧与被测模型侧。
    返回 "simulator" | "agent" | "unclear"。

    agent 侧信号（会话没给模拟器机会，不是模拟器的错）：
    - exception.txt 有 NonZeroAgentExitCodeError/Timeout/max_turns；
    - 轨迹最后一个 result 的 terminal_reason=max_turns（agent 自闭不 yield）。
    simulator 侧信号：execution_log 有 simulator_broken 收尾 / 高比例
    empty_message / simulator_refusal。
    两类同时出现按 agent 优先（会话先断在 agent 手里）。"""
    agent_side = False
    exc = trial_dir / "exception.txt"
    if exc.is_file():
        head = exc.read_text(encoding="utf-8", errors="replace")[:4000]
        if any(
            k in head
            for k in ("NonZeroAgentExitCodeError", "TimeoutError", "AgentTimeoutError")
        ):
            agent_side = True
    turns = sorted((trial_dir / "agent").glob("claude-code-turn-*.txt"))
    if turns and not agent_side:
        try:
            for line in open(turns[-1], errors="replace"):
                if (
                    '"terminal_reason":"max_turns"' in line
                    or '"terminal_reason": "max_turns"' in line
                ):
                    agent_side = True
                    break
        except OSError:
            pass
    sim_side = False
    for e in elog:
        if e.get("simulator_refusal") or e.get("empty_message"):
            sim_side = True
        if "simulator_broken" in str(e.get("reason") or ""):
            sim_side = True
    if agent_side:
        return "agent"
    if sim_side:
        return "simulator"
    return "unclear"


def _assemble_prompt(
    *,
    instance_dir: Path,
    trial_dir: Path,
    out_path: Path,
    config: JudgeRuntimeConfig,
) -> tuple[str, dict[str, Any]]:
    """组装 judge prompt，返回 (prompt, context)。

    context 含后续校验/回填所需的 script/rubrics/dom_index/g_scenarios/
    expected_items/is_v04/elog，避免调用方重复解析。"""
    script_path = instance_dir / "interaction" / "script.json"
    if not script_path.is_file():
        raise JudgeError(f"interaction/script.json 不存在: {script_path}")
    script = json.loads(script_path.read_text(encoding="utf-8"))

    rubrics_path = _resolve_rubrics_path(instance_dir)
    # v0.4（probes 按场景 ID 组织）的场景定义来自 domains.md，不依赖
    # rubrics.json；仅旧版维度名剧本需要 rubrics.json。
    probes_for_detect = script.get("probes") or {}
    needs_rubrics = not all(SCENARIO_ID_RE.match(str(k)) for k in probes_for_detect)
    if rubrics_path is None and needs_rubrics:
        raise JudgeError(f"rubrics.json 未找到（从 {instance_dir} 向上查找均无）")
    rubrics = (
        json.loads(rubrics_path.read_text(encoding="utf-8")) if rubrics_path else {}
    )

    probes = script["probes"]
    is_v04 = bool(probes) and all(SCENARIO_ID_RE.match(str(k)) for k in probes)
    dom_index: dict[str, Any] = {}
    g_scenarios: list[str] = []
    if is_v04:
        domains_md, selection_json = _resolve_domains_paths(instance_dir)
        dom_index = scenario_index(load_domains(domains_md))
        sel = set(load_selection(selection_json).get("scenarios") or [])
        g_scenarios = [s for s in sorted(sel) if s in dom_index and dom_index[s]["g"]]

    # runner 侧 sim 账本提前加载：prompt 需要信息片释放统计
    # （sim_slice_coverage 的事实输入），判后机械兜底（探针未发）复用同一份。
    elog: list[dict[str, Any]] = _load_execution_log(trial_dir)

    expected_items: dict[str, set[str]] = {}
    dim_sections = []
    for dim, probe in probes.items():
        if is_v04:
            items = [{**it, "source": "script"} for it in probe.get("checklist", [])]
        else:
            items = collect_checklist(dim, probe, rubrics)
        if not items:
            if is_v04:
                raise JudgeError(
                    f"场景 '{dim}' 没有 checklist：v0.4 剧本必须在 "
                    f"script.json probes 里为该场景定制 checklist"
                )
            raise JudgeError(
                f"维度 '{dim}' 没有 checklist：主动探针维度必须在 script.json "
                f"probes 里定制 checklist，被动/固定维度用 rubrics.json 全局项"
            )
        expected_items[dim] = {it["id"] for it in items}
        item_lines = []
        for it in items:
            hc = it.get("hard_check") or "无"
            item_lines.append(
                f"- [{it['id']}]（证据来源: {it.get('evidence_from', 'transcript')}；"
                f"hard_check: {hc}）\n"
                f"  判定问题: {it['question']}\n"
                f"  判 0 条件: {it.get('zero_when', '见判定问题')}"
            )
        if is_v04:
            meta = dom_index.get(dim, {})
            dim_sections.append(
                f"### 场景 {dim}（{meta.get('dim_name', '?')} / "
                f"{meta.get('subdim_name', '?')}）\n"
                f"场景定义: {meta.get('text', '（domains.md 未找到）')}\n"
                f"目标: {probe.get('goal', '')}\n"
                f"期望行为: {probe.get('expected', '')}\n" + "\n".join(item_lines)
            )
        else:
            definition = dimension_definition(dim, rubrics)
            turns = "、".join(probe.get("turns", []))
            dim_sections.append(
                f"### {dim}\n维度定义: {definition}\n相关轮次: {turns}\n"
                + "\n".join(item_lines)
            )
    # v0.4：G 场景（无探针，通用观察）追加为单条目评判单元
    for sid in g_scenarios:
        meta = dom_index[sid]
        item_id = f"g_{sid.replace('.', '_')}"
        expected_items[sid] = {item_id}
        dim_sections.append(
            f"### 场景 {sid}（G 通用观察：{meta['dim_name']} / "
            f"{meta['subdim_name']}；本题未挂探针，从整条轨迹评估）\n"
            f"场景定义: {meta['text']}\n"
            f"- [{item_id}]（证据来源: transcript）\n"
            f"  判定问题: {meta['text']}\n"
            f"  判 0 条件: 轨迹中出现场景定义所述负向行为；"
            f"相关情形本次确实未发生才判 n/a"
        )

    evidence_rel = "interaction_evidence/"
    if not (trial_dir / evidence_rel).exists():
        # verifier 挂载目录下的证据包（collect_evidence.sh 的标准落点）
        for candidate in (
            "verifier/interaction_evidence/",
            "interaction_evidence/",
        ):
            if (trial_dir / candidate / "hard_checks.json").exists():
                evidence_rel = candidate
                break
        else:
            candidates = list(trial_dir.glob("**/hard_checks.json"))
            if candidates:
                evidence_rel = str(candidates[0].parent.relative_to(trial_dir)) + "/"
            else:
                logger.warning("未在 %s 下找到证据包", trial_dir)

    n_units = len(expected_items)
    # 逐轮输出位置按 agent 类型分流：claude-code-usersim 每轮写
    # agent/claude-code-turn-N.txt；terminus-2-usersim 每轮写
    # agent/command-N/stdout.txt（trajectory.json 为主数据源）。按盘上实况
    # 判定，claude 路径提示文案保持不变。
    agent_dir = trial_dir / "agent"
    if agent_dir.is_dir() and sorted(agent_dir.glob("claude-code-turn-*.txt")):
        transcript_hint = (
            "agent/ 目录下的 claude-code-turn-*.txt（逐轮输出）；"
            "result.json 的 agent_result.metadata.user_simulation.conversation"
            "（用户模拟器与 agent 的逐轮对话——评估模拟器必须以此为据）"
        )
    else:
        transcript_hint = (
            "agent/ 目录下的 command-*/stdout.txt（逐轮输出）与 "
            "agent/trajectory.json（ATIF 轨迹，含每 episode 的命令/观测）；"
            "result.json 的 agent_result.metadata.user_simulation.conversation"
            "（用户模拟器与 agent 的逐轮对话——评估模拟器必须以此为据）"
        )
    prompt = _load_prompt_template().substitute(
        n_dims=n_units,
        unit="场景" if is_v04 else "维度",
        dimensions="\n\n".join(dim_sections),
        script_path=str(script_path),
        rubrics_path=str(rubrics_path) if rubrics_path else "（本题不需要）",
        evidence_rel=evidence_rel,
        transcript_hint=transcript_hint,
        instance_id=script.get("instance_id", ""),
        slice_delivery=_slice_delivery_note(elog, script),
        write_instruction=write_instruction_for_backend(config.backend, str(out_path)),
    )
    context = {
        "script": script,
        "dom_index": dom_index,
        "g_scenarios": g_scenarios,
        "expected_items": expected_items,
        "is_v04": is_v04,
        "elog": elog,
    }
    return prompt, context


def _repair_item_grouping(
    scores: dict[str, Any], expected_items: dict[str, set[str]]
) -> dict[str, Any]:
    """只按唯一item归属修复错误分组；不猜分、不补缺项、不覆盖重复项。"""
    scores = copy.deepcopy(scores)
    rows = scores.get("scores")
    if not isinstance(rows, list) or any(
        not isinstance(r, dict) or not isinstance(r.get("dimension"), str) for r in rows
    ):
        raise JudgeError("judge 输出缺少 scores[].dimension 结构")
    owners: dict[str, list[str]] = {}
    for dim, ids in expected_items.items():
        for iid in ids:
            owners.setdefault(iid, []).append(dim)
    grouped = {}
    seen = set()
    moved = []
    for row in rows:
        dim = row["dimension"]
        grouped.setdefault(dim, {**row, "items": []})
        if not isinstance(row.get("items", []), list):
            raise JudgeError(f"场景 {dim} 的items不是数组")
        for item in row.get("items", []):
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise JudgeError(f"场景 {dim} 出现无效item结构")
            iid = item["id"]
            target = dim
            if (
                iid not in expected_items.get(dim, set())
                and len(owners.get(iid, [])) == 1
            ):
                target = owners[iid][0]
                moved.append(f"{iid}: {dim} -> {target}")
            if (target, iid) in seen:
                raise JudgeError(f"重复item: {target}/{iid}")
            seen.add((target, iid))
            grouped.setdefault(target, {"dimension": target, "items": []})[
                "items"
            ].append(item)
    scores["scores"] = list(grouped.values())
    if moved:
        scores["normalization_notes"] = (
            list(scores.get("normalization_notes") or []) + moved
        )
    return scores


def _contract_digest(context: dict[str, Any]) -> str:
    data = {
        "script": context["script"],
        "domains": context.get("dom_index", {}),
        "g_scenarios": context["g_scenarios"],
        "version": 2,
        "prompt": hashlib.sha256(PROMPT_TEMPLATE_PATH.read_bytes()).hexdigest(),
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def validate_existing_scores(
    instance_dir: Path,
    trial_dir: Path,
    out_path: Path | None = None,
    *,
    require_stamp: bool = True,
) -> dict[str, Any]:
    """缓存与新结果使用同一校验；旧口径/未完成后处理的文件不能冒充有效缓存。"""
    path = out_path or trial_dir / DIMENSION_SCORES_FILENAME
    try:
        scores = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise JudgeError(f"评分缓存不可读: {path}: {exc}") from exc
    _, context = _assemble_prompt(
        instance_dir=instance_dir,
        trial_dir=trial_dir,
        out_path=path,
        config=JudgeRuntimeConfig(),
    )
    if not isinstance(scores, dict):
        raise JudgeError("评分缓存必须是 JSON 对象")
    if require_stamp and (scores.get("_validation") or {}).get(
        "contract_sha256"
    ) != _contract_digest(context):
        raise JudgeError("评分缓存未完成校验或评分契约已改变，必须重新评分")
    return _validate_and_backfill(
        scores, out_path=path, trial_dir=trial_dir, context=context
    )


def _validate_and_backfill(
    scores: dict[str, Any],
    *,
    out_path: Path,
    trial_dir: Path,
    context: dict[str, Any],
) -> dict[str, Any]:
    """judge 输出硬校验 + 探针未发兜底 + passed/applicable/dimension_score
    回填。校验失败抛 JudgeError（清晰报错，替代旧版 sys.exit 致命退出
    导致后处理整段跳过）。"""
    scores = _repair_item_grouping(scores, context["expected_items"])
    script = context["script"]
    if scores.get("instance_id") not in (None, script.get("instance_id")):
        raise JudgeError("judge输出instance_id与题目不符")
    is_v04 = context["is_v04"]
    expected_items = context["expected_items"]
    g_scenarios = context["g_scenarios"]
    elog = context["elog"]
    unit = "场景" if is_v04 else "维度"

    try:
        dims = {s["dimension"] for s in scores["scores"]}
    except (KeyError, TypeError) as exc:
        raise JudgeError(
            f"judge 输出缺少 scores[].dimension 结构（疑似维度键嵌套——"
            f"judge 把多个{unit}的 items 合并到了同一 dimension 键下）: "
            f"{out_path}"
        ) from exc
    expected = set(script["probes"].keys())
    if is_v04:
        expected |= set(g_scenarios)
    if dims != expected:
        raise JudgeError(
            f"{unit}不齐: missing={sorted(expected - dims)} "
            f"extra={sorted(dims - expected)}（judge 必须为每个{unit}输出独立"
            f"的 scores 条目，不得把多个{unit}嵌套合并到同一 dimension 键）"
        )

    # v0.4 硬规则：探针未发出的场景，其 checklist 项按 judge 实质评分
    # 保留+标注或强制 n/a，场景计入 contaminated。不信 judge 自觉，
    # judge 输出回来后代码兜底改写。
    unfired: set[str] = set()
    if is_v04:
        fired = _fired_probe_ids(elog, script.get("probes") or {})
        # 只有带 stimulus 的探针才参与"未发"判定（与 harbor runner 的
        # all_probes 口径一致；无 stimulus 的探针 runner 从不追踪）
        unfired = {
            sid
            for sid, spec in (script["probes"] or {}).items()
            if isinstance(spec, dict) and requires_delivery(spec) and sid not in fired
        }
    for s in scores["scores"]:
        dim = s["dimension"]
        judged = {}
        for it in s.get("items", []):
            value = it.get("pass")
            if isinstance(value, str) and value.strip().lower() in ("0", "1", "n/a"):
                value = value.strip().lower()
                value = int(value) if value in ("0", "1") else value
            if isinstance(value, bool):
                value = int(value)
            if value not in (0, 1, "n/a"):
                raise JudgeError(f"场景 {dim} 的item {it['id']} 分值无效: {value!r}")
            if not isinstance(it.get("evidence"), str) or not it["evidence"].strip():
                raise JudgeError(f"场景 {dim} 的item {it['id']} 缺证据")
            it["pass"] = value
            judged[it["id"]] = value
        unknown = set(judged) - expected_items[dim]
        missing = expected_items[dim] - set(judged)
        if unknown:
            raise JudgeError(f"{unit} '{dim}' 出现未知 item id: {sorted(unknown)}")
        if missing:
            raise JudgeError(f"{unit} '{dim}' 漏判 item: {sorted(missing)}")
        if dim in unfired:
            # 探针未发（2026-09-18 用户拍板放宽）：judge 基于 agent 自发行为
            # 给出实质评分（0/1 且证据非空）的保留并标注，不再一律抹掉；
            # judge 自己判 n/a 或证据空泛的维持强制 n/a。维度仍计入
            # contaminated 供聚合剔除/参考。
            for it in s.get("items", []):
                ev = str(it.get("evidence") or "")
                if it.get("pass") in (0, 1) and len(ev.strip()) >= 40:
                    if not ev.startswith("[pipeline 注]"):
                        it["evidence"] = (
                            "[pipeline 注] 该场景探针本次会话未发出；以下为 judge "
                            "基于 agent 自发行为的实质评分（维度仍计入 "
                            "contaminated）：" + ev
                        )
                    judged[it["id"]] = it.get("pass")
                else:
                    it["pass"] = "n/a"
                    it["evidence"] = (
                        "[pipeline 强制] 该场景探针本次会话未发出，"
                        "情形未发生，判 n/a（不是 agent 的错）"
                    )
                    judged[it["id"]] = "n/a"
        applicable = [v for v in judged.values() if v in (0, 1)]
        passed = sum(applicable)
        s["passed"] = passed
        s["applicable"] = len(applicable)
        s["dimension_score"] = (
            round(passed / len(applicable), 4) if applicable else None
        )

    # 用户模拟器评估：只校验结构，不干预判定；警告便于人工关注
    sim_eval = scores.get("simulator_evaluation")
    if unfired:
        # 探针未发（2026-09-07 用户口径）：simulator_not_ok 完全交给 judge
        # 主判（prompt 里已写归因规则）。代码只做机械兜底：未发场景的
        # checklist 项强制 n/a + 计入 contaminated；不再强改 sim ok/coverage。
        attrib = _unfired_attribution(trial_dir, elog)
        sim_eval = sim_eval or {}
        sim_eval["contaminated_dimensions"] = sorted(
            set(sim_eval.get("contaminated_dimensions") or []) | unfired
        )
        scores["simulator_evaluation"] = sim_eval
        logger.warning(
            "探针未发: %s → 项判 n/a + contaminated；代码侧归因参考=%s"
            "（not_ok 由 judge 主判）",
            sorted(unfired),
            attrib,
        )
    if not sim_eval or not isinstance(sim_eval.get("ok"), bool):
        raise JudgeError("judge输出缺少完整simulator_evaluation/ok")
    else:
        checks = sim_eval.get("checks")
        if not isinstance(checks, list) or any(
            not isinstance(c, dict) or not isinstance(c.get("id"), str) for c in checks
        ):
            raise JudgeError("sim checks结构无效")
        judged_sim = {c["id"] for c in checks}
        if judged_sim != SIM_CHECK_IDS or len(checks) != len(judged_sim):
            raise JudgeError(
                f"sim 检查项不齐或重复: missing={sorted(SIM_CHECK_IDS - judged_sim)} extra={sorted(judged_sim - SIM_CHECK_IDS)}"
            )
        for check in checks:
            value = check.get("pass")
            if isinstance(value, str) and value.strip().lower() in ("0", "1", "n/a"):
                value = value.strip().lower()
                value = int(value) if value in ("0", "1") else value
            if isinstance(value, bool):
                value = int(value)
            if (
                value not in (0, 1, "n/a")
                or not isinstance(check.get("evidence"), str)
                or not check["evidence"].strip()
            ):
                raise JudgeError(f"sim检查{check['id']}缺少有效分值/证据")
            check["pass"] = value
        contaminated = set(sim_eval.get("contaminated_dimensions", []))
        unknown_contaminated = contaminated - expected
        if unknown_contaminated:
            logger.warning(
                "contaminated_dimensions 含未知维度: %s",
                sorted(unknown_contaminated),
            )
        if not sim_eval.get("ok", True):
            logger.warning(
                "用户模拟器评估未通过，被污染维度: %s",
                sorted(contaminated) or "见 note",
            )
    scores["_validation"] = {"version": 2, "contract_sha256": _contract_digest(context)}
    return scores


async def judge_trial(
    *,
    instance_dir: Path,
    trial_dir: Path,
    out_path: Path,
    config: JudgeRuntimeConfig,
    backend_runner: BackendRunner | None = None,
) -> dict[str, Any]:
    """只原子发布验证完成的结果；原始/错误输出单独留存，非零rc始终抛出。"""
    instance_dir, trial_dir, out_path = (
        instance_dir.resolve(),
        trial_dir.resolve(),
        out_path.resolve(),
    )
    pending = out_path.with_name(out_path.stem + ".pending.json")
    prompt, context = _assemble_prompt(
        instance_dir=instance_dir, trial_dir=trial_dir, out_path=pending, config=config
    )
    (trial_dir / JUDGE_PROMPT_FILENAME).write_text(prompt, encoding="utf-8")
    env = build_judge_env(
        model_name=config.model_name,
        max_output_tokens=config.max_output_tokens,
        config_dir=config.config_dir,
    )
    env["HARBOR_JUDGE_OUTPUT_PATH"] = str(pending)
    problem = ""
    for attempt in range(3):
        pending.unlink(missing_ok=True)
        request = prompt + (
            f"\nPrevious output failed validation: {problem}. Write a complete corrected result with every requested item and all nine simulator checks.\n"
            if problem
            else ""
        )
        if backend_runner is not None:
            await backend_runner(request, trial_dir, config, env)
        elif config.backend == "claude_cli":
            await _run_claude_judge(request, trial_dir, config, env)
        elif config.backend == "codex_cli":
            await _run_codex_judge(request, trial_dir, config, env)
        else:
            raise JudgeError(f"unsupported judge backend: {config.backend}")
        try:
            if not pending.is_file():
                raise JudgeError(f"judge 未写出 {pending}")
            try:
                scores = json.loads(pending.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise JudgeError(f"judge输出不是合法JSON: {exc}") from exc
            if not isinstance(scores, dict):
                raise JudgeError("judge输出必须为JSON对象")
            scores = _validate_and_backfill(
                scores, out_path=pending, trial_dir=trial_dir, context=context
            )
        except JudgeError as exc:
            problem = str(exc)
            if pending.exists():
                pending.replace(
                    out_path.with_name(out_path.stem + f".invalid-{attempt + 1}.json")
                )
            if attempt == 2:
                raise
            continue
        pending.write_text(
            json.dumps(scores, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(pending, out_path)
        logger.info("judge scores written: %s", out_path)
        return scores
    raise JudgeError(problem)


async def run_judge_for_trial(
    task_dir: Path,
    trial_dir: Path,
    config: JudgeRuntimeConfig,
    *,
    backend_runner: BackendRunner | None = None,
) -> JudgeResult:
    """trial 内联调用入口：对 <task_dir, trial_dir> 跑 judge，落盘
    <trial>/dimension_scores.json 并返回校验后的 JudgeResult。"""
    scores = await judge_trial(
        instance_dir=task_dir,
        trial_dir=trial_dir,
        out_path=trial_dir.resolve() / DIMENSION_SCORES_FILENAME,
        config=config,
        backend_runner=backend_runner,
    )
    return JudgeResult.model_validate(scores)


def main(argv: list[str] | None = None) -> int:
    """CLI 入口（与旧 clibench judge/run_judge.py 参数同名）。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True, type=Path)
    parser.add_argument("--trial", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="仅校验已有结果及契约指纹，不调用模型",
    )
    parser.add_argument(
        "--judge-backend",
        choices=("claude_cli", "codex_cli"),
        default="claude_cli",
        help="judge backend；smoke 阶段可用 codex_cli，legacy/fallback 用 claude_cli",
    )
    parser.add_argument(
        "--claude-args",
        default="",
        help="额外传给 claude CLI 的参数（如 '--model xxx'）",
    )
    parser.add_argument(
        "--codex-args",
        default="",
        help="额外传给 codex exec 的参数（如 '--model gpt-5.5'）",
    )
    args = parser.parse_args(argv)

    trial_dir = args.trial.resolve()
    config = JudgeRuntimeConfig(
        backend=args.judge_backend,
        model_name=os.environ.get("HARBOR_JUDGE_MODEL", DEFAULT_JUDGE_MODEL),
        timeout_sec=float(
            os.environ.get("HARBOR_JUDGE_TIMEOUT_SEC", DEFAULT_TIMEOUT_SEC)
        ),
        max_output_tokens=int(
            os.environ.get("HARBOR_JUDGE_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS)
        ),
        claude_args=args.claude_args,
        codex_args=args.codex_args,
    )
    try:
        if args.validate_only:
            validate_existing_scores(
                args.instance.resolve(),
                trial_dir,
                args.out or trial_dir / DIMENSION_SCORES_FILENAME,
            )
            print("judge cache validated")
            return 0
        scores = asyncio.run(
            judge_trial(
                instance_dir=args.instance,
                trial_dir=trial_dir,
                out_path=args.out or (trial_dir / DIMENSION_SCORES_FILENAME),
                config=config,
            )
        )
    except JudgeBackendError as exc:
        print(str(exc), file=sys.stderr)
        return exc.returncode if exc.returncode > 0 else 1
    except JudgeError as exc:
        print(f"judge 失败: {exc}", file=sys.stderr)
        return 1

    for s in scores["scores"]:
        contaminated = (
            " [CONTAMINATED]"
            if scores.get("simulator_evaluation")
            and s["dimension"]
            in set(scores["simulator_evaluation"].get("contaminated_dimensions", []))
            else ""
        )
        print(
            f"  {s['dimension']}: {s['passed']}/{s['applicable']}"
            f" = {s['dimension_score']}{contaminated}"
        )
    sim_eval = scores.get("simulator_evaluation")
    if sim_eval:
        print(f"  用户模拟器评估: {'OK' if sim_eval.get('ok') else 'NOT OK'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
