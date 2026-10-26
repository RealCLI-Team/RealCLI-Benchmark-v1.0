"""--agent-max-timeout-sec 语义单测（2026-09-15）。

对齐平台 SANDBOX_AGENT_EXEC_TIMEOUT_SEC=36000：cap 作用在 multiplier 之前
（min(base, cap) × multiplier），主流 4h 题 → 10h，小基数题不受影响。

移植说明：原版经内部 SingleModeRunner 调用；本树里对应实现是
``Trial._compute_agent_timeout_sec`` / ``Trial._resolve_timeout_sec``，
测试改为直接打桩调用（不再依赖内部 single_mode 模块）。
"""

from __future__ import annotations

from types import SimpleNamespace

from harbor.models.trial.config import AgentConfig, TaskConfig, TrialConfig
from harbor.trial.trial import Trial


class _TrialStub:
    """Minimal Trial stand-in exposing config/task and the real resolver."""

    _resolve_timeout_sec = Trial._resolve_timeout_sec

    def __init__(self, config: TrialConfig, task: SimpleNamespace) -> None:
        self.config = config
        self.task = task


def _make(
    trial_agent: AgentConfig, task_agent_timeout: float | None, multiplier: float
) -> float | None:
    trial_config = TrialConfig(
        task=TaskConfig(path="/tmp/dummy-task"),
        trials_dir="/tmp/dummy-trials",
        agent=trial_agent,
        timeout_multiplier=multiplier,
    )
    task = SimpleNamespace(
        config=SimpleNamespace(agent=SimpleNamespace(timeout_sec=task_agent_timeout))
    )
    stub = _TrialStub(trial_config, task)
    return Trial._compute_agent_timeout_sec(stub)  # type: ignore[arg-type]


def test_cap_pulls_mainstream_4h_task_to_10h() -> None:
    got = _make(AgentConfig(max_timeout_sec=3000), 14400, 12.0)
    assert got == 3000 * 12.0 == 36000.0


def test_small_base_task_unaffected_by_cap() -> None:
    got = _make(AgentConfig(max_timeout_sec=3000), 600, 12.0)
    assert got == 7200.0


def test_no_cap_keeps_previous_behavior() -> None:
    got = _make(AgentConfig(), 14400, 12.0)
    assert got == 172800.0


def test_override_timeout_sec_wins_over_task_base() -> None:
    got = _make(
        AgentConfig(override_timeout_sec=5000, max_timeout_sec=3000), 14400, 12.0
    )
    assert got == 36000.0


def test_no_base_timeout_returns_none() -> None:
    assert _make(AgentConfig(max_timeout_sec=3000), None, 12.0) is None
