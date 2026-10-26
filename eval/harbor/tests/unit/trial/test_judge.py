"""Trial 生命周期内联 judge 的接线测试（object.__new__ 组装风格）。"""

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from harbor.models.task.config import JudgeConfig
from harbor.models.task.verifier_mode import VerifierEnvironmentMode
from harbor.trial.single_step import SingleStepTrial


def _trial(tmp_path: Path, *, with_script: bool) -> SingleStepTrial:
    task_dir = tmp_path / "task"
    if with_script:
        (task_dir / "interaction").mkdir(parents=True)
        (task_dir / "interaction" / "script.json").write_text("{}")
    else:
        task_dir.mkdir()

    trial = object.__new__(SingleStepTrial)
    trial.logger = MagicMock()
    trial.task = SimpleNamespace(
        config=SimpleNamespace(judge=None),
        paths=SimpleNamespace(task_dir=task_dir),
    )
    trial.paths = SimpleNamespace(trial_dir=tmp_path / "trial")
    trial._result = SimpleNamespace()
    return trial


@pytest.mark.asyncio
async def test_run_judge_skips_when_no_script_json(tmp_path: Path) -> None:
    trial = _trial(tmp_path, with_script=False)

    await trial._run_judge()

    assert getattr(trial._result, "judge", None) is None
    assert getattr(trial._result, "judge_result", None) is None


@pytest.mark.asyncio
async def test_run_judge_skips_when_explicitly_disabled(
    tmp_path: Path, monkeypatch
) -> None:
    trial = _trial(tmp_path, with_script=True)
    monkeypatch.setenv("HARBOR_JUDGE_ENABLED", "0")

    await trial._run_judge()

    assert getattr(trial._result, "judge", None) is None


@pytest.mark.asyncio
async def test_run_judge_forces_on_via_env_without_script(
    tmp_path: Path, monkeypatch
) -> None:
    trial = _trial(tmp_path, with_script=False)
    monkeypatch.setenv("HARBOR_JUDGE_ENABLED", "1")
    from harbor.judge.models import JudgeResult

    fake = AsyncMock(return_value=JudgeResult(instance_id="x"))
    monkeypatch.setattr("harbor.judge.runner.run_judge_for_trial", fake)

    await trial._run_judge()

    fake.assert_awaited_once()
    assert trial._result.judge_result is not None
    assert trial._result.judge.started_at is not None
    assert trial._result.judge.finished_at is not None


@pytest.mark.asyncio
async def test_run_judge_auto_detects_and_passes_dirs(
    tmp_path: Path, monkeypatch
) -> None:
    trial = _trial(tmp_path, with_script=True)
    captured = {}

    async def fake_run(task_dir, trial_dir, config):
        captured["task_dir"] = task_dir
        captured["trial_dir"] = trial_dir
        return None

    monkeypatch.setattr("harbor.judge.runner.run_judge_for_trial", fake_run)

    await trial._run_judge()

    assert captured["task_dir"] == trial.task.paths.task_dir
    assert captured["trial_dir"] == trial.paths.trial_dir


@pytest.mark.asyncio
async def test_run_judge_failure_is_best_effort(tmp_path: Path, monkeypatch) -> None:
    trial = _trial(tmp_path, with_script=True)
    monkeypatch.setattr(
        "harbor.judge.runner.run_judge_for_trial",
        AsyncMock(side_effect=RuntimeError("judge boom")),
    )

    await trial._run_judge()  # 不抛

    assert trial._result.judge_result is None
    assert getattr(trial._result, "exception_info", None) is None
    assert trial._result.judge.finished_at is not None


@pytest.mark.asyncio
async def test_run_calls_judge_after_verifier_before_teardown(
    tmp_path: Path, monkeypatch
) -> None:
    trial = _trial(tmp_path, with_script=True)
    events: list[str] = []

    trial._run_agent = AsyncMock(side_effect=lambda: events.append("agent"))
    trial._upload_agent_logs = AsyncMock()
    trial._collect_artifacts = AsyncMock()
    trial._run_verifier = AsyncMock(side_effect=lambda: events.append("verifier"))
    trial._run_judge = AsyncMock(side_effect=lambda: events.append("judge"))
    trial._stop_agent_environment = AsyncMock(
        side_effect=lambda: events.append("teardown")
    )
    trial._are_artifacts_collected = False
    monkeypatch.setattr(
        "harbor.trial.single_step.resolve_task_verifier_mode",
        lambda _config: VerifierEnvironmentMode.SHARED,
    )

    await trial._run()

    assert events == ["agent", "verifier", "judge", "teardown"]


@pytest.mark.asyncio
async def test_judge_result_survives_into_result_json(tmp_path: Path) -> None:
    """judge_result 字段可随 TrialResult 序列化落盘（result.json）。"""
    from harbor.judge.models import (
        JudgeDimensionScore,
        JudgeItemResult,
        JudgeResult,
        SimulatorEvaluation,
    )

    result = JudgeResult(
        instance_id="iid1",
        scores=[
            JudgeDimensionScore(
                dimension="2.2.1",
                items=[JudgeItemResult(id="a", verdict=1, evidence="e")],
                passed=1,
                applicable=1,
                dimension_score=1.0,
            )
        ],
        simulator_evaluation=SimulatorEvaluation(ok=True),
    )
    payload = json.loads(result.model_dump_json())
    assert payload["scores"][0]["items"][0]["pass"] == 1
    assert payload["scores"][0]["dimension_score"] == 1.0
    assert payload["simulator_evaluation"]["ok"] is True


def test_judge_config_env_overrides(tmp_path: Path, monkeypatch) -> None:
    trial = _trial(tmp_path, with_script=True)
    monkeypatch.setenv("HARBOR_JUDGE_MODEL", "judge-model-x")
    monkeypatch.setenv("HARBOR_JUDGE_TIMEOUT_SEC", "1800")
    monkeypatch.setenv("HARBOR_JUDGE_MAX_OUTPUT_TOKENS", "32000")

    resolved, meta = trial._resolve_judge_config(JudgeConfig())

    assert resolved.model_name == "judge-model-x"
    assert resolved.timeout_sec == 1800.0
    assert resolved.max_output_tokens == 32000
    assert meta["applied"]["HARBOR_JUDGE_MODEL"] == "judge-model-x"


def test_judge_config_invalid_env_ignored(tmp_path: Path, monkeypatch) -> None:
    trial = _trial(tmp_path, with_script=True)
    monkeypatch.setenv("HARBOR_JUDGE_TIMEOUT_SEC", "not-a-number")

    resolved, meta = trial._resolve_judge_config(JudgeConfig())

    assert resolved.timeout_sec == 3600
    assert "HARBOR_JUDGE_TIMEOUT_SEC" in meta["ignored"]


def test_timing_info_default() -> None:
    from harbor.models.trial.result import TimingInfo

    t = TimingInfo(started_at=datetime.now())
    assert t.finished_at is None
