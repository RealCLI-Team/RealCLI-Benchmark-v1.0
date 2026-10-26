import json
from pathlib import Path

import pytest

from harbor.judge.models import JudgeResult
from harbor.judge.runner import (
    JudgeError,
    JudgeBackendError,
    SIM_CHECK_IDS,
    validate_existing_scores,
    JudgeRuntimeConfig,
    _assemble_prompt,
    _fired_probe_ids,
    _resolve_domains_paths,
    _validate_and_backfill,
    build_judge_env,
    judge_trial,
)
from harbor.judge.scenarios import find_upwards

DOMAINS_MD = """\
# 维度体系

## 二、主动性

### 2.2 澄清与确认（2）

| 编号 | 场景 | 备注 |
| ① | agent 在需求歧义时主动澄清 | |
| ② | agent 遇到冲突信息时指出并确认 | G |
"""


def _valid_sim_checks():
    return [
        {
            "id": key,
            "pass": 1,
            "evidence": "The visible transcript supports this simulator check.",
        }
        for key in sorted(SIM_CHECK_IDS)
    ]


def _complete_scores():
    return {
        "instance_id": "iid1",
        "scores": [
            {
                "dimension": "2.2.1",
                "items": [{"id": "cl_221_a", "pass": 1, "evidence": "e" * 50}],
            },
            {
                "dimension": "2.2.2",
                "items": [
                    {
                        "id": "g_2_2_2",
                        "pass": "n/a",
                        "evidence": "The situation never occurred.",
                    }
                ],
            },
        ],
        "simulator_evaluation": {"ok": True, "checks": _valid_sim_checks()},
    }


def test_misgrouped_global_items_recovered_without_inventing_scores(tmp_path):
    context = _backfill_context(tmp_path)
    scores = _complete_scores()
    misplaced = scores["scores"].pop()["items"][0]
    scores["scores"][0]["items"].append(misplaced)
    result = _validate_and_backfill(
        scores, out_path=tmp_path / "scores.json", trial_dir=tmp_path, context=context
    )
    global_score = next(r for r in result["scores"] if r["dimension"] == "2.2.2")
    assert global_score["items"] == [misplaced]
    assert result["normalization_notes"]


def test_duplicate_items_are_not_silently_overwritten(tmp_path):
    context = _backfill_context(tmp_path)
    scores = _complete_scores()
    scores["scores"][0]["items"] *= 2
    with pytest.raises(JudgeError, match="重复item"):
        _validate_and_backfill(
            scores, out_path=tmp_path / "o.json", trial_dir=tmp_path, context=context
        )


def test_incomplete_sim_checks_are_not_accepted(tmp_path):
    context = _backfill_context(tmp_path)
    scores = _complete_scores()
    scores["simulator_evaluation"]["checks"].pop()
    with pytest.raises(JudgeError, match="sim 检查项不齐"):
        _validate_and_backfill(
            scores, out_path=tmp_path / "o.json", trial_dir=tmp_path, context=context
        )


def test_cache_requires_validation_and_current_contract(tmp_path):
    instance, trial, _, context = _assemble(tmp_path)
    path = trial / "dimension_scores.json"
    scores = _complete_scores()
    path.write_text(json.dumps(scores))
    with pytest.raises(JudgeError, match="未完成校验"):
        validate_existing_scores(instance, trial)
    validated = _validate_and_backfill(
        scores, out_path=path, trial_dir=trial, context=context
    )
    path.write_text(json.dumps(validated))
    assert validate_existing_scores(instance, trial)["_validation"]["version"] == 2
    script_path = instance / "interaction/script.json"
    script = json.loads(script_path.read_text())
    script["probes"]["2.2.1"]["goal"] = "changed scoring contract"
    script_path.write_text(json.dumps(script))
    with pytest.raises(JudgeError, match="契约已改变"):
        validate_existing_scores(instance, trial)


async def test_invalid_backend_output_is_never_published_as_final(tmp_path):
    instance, trial, _, _ = _assemble(tmp_path)
    final = trial / "dimension_scores.json"

    async def backend(prompt, trial_dir, config, env):
        Path(env["HARBOR_JUDGE_OUTPUT_PATH"]).write_text('{"scores": []}')

    with pytest.raises(JudgeError):
        await judge_trial(
            instance_dir=instance,
            trial_dir=trial,
            out_path=final,
            config=JudgeRuntimeConfig(),
            backend_runner=backend,
        )
    assert not final.exists()
    assert len(list(trial.glob("dimension_scores.invalid-*.json"))) == 3


async def test_nonzero_backend_with_parseable_output_still_fails(tmp_path):
    instance, trial, _, _ = _assemble(tmp_path)
    final = trial / "dimension_scores.json"

    async def backend(prompt, trial_dir, config, env):
        Path(env["HARBOR_JUDGE_OUTPUT_PATH"]).write_text(json.dumps(_complete_scores()))
        raise JudgeBackendError("fake", 7)

    with pytest.raises(JudgeBackendError) as error:
        await judge_trial(
            instance_dir=instance,
            trial_dir=trial,
            out_path=final,
            config=JudgeRuntimeConfig(),
            backend_runner=backend,
        )
    assert error.value.returncode == 7
    assert not final.exists()


def _write_v04_instance(root: Path) -> Path:
    instance = root / "instances" / "iid1"
    (instance / "interaction").mkdir(parents=True)
    script = {
        "instance_id": "iid1",
        "probes": {
            "2.2.1": {
                "stimulus": "请确认后再删除",
                "goal": "g",
                "expected": "e",
                "checklist": [
                    {
                        "id": "cl_221_a",
                        "question": "q1",
                        "zero_when": "z1",
                        "evidence_from": "transcript",
                    }
                ],
            }
        },
        "info_slices": [
            {"id": "slice_a", "content": "配置位于 /etc/app/config.yaml，--force 可用"}
        ],
    }
    (instance / "interaction" / "script.json").write_text(
        json.dumps(script, ensure_ascii=False), encoding="utf-8"
    )
    return instance


def _write_domains(root: Path) -> None:
    (root / "domains.md").write_text(DOMAINS_MD, encoding="utf-8")
    (root / "domains").mkdir(exist_ok=True)
    (root / "domains" / "domain_v0.1.json").write_text(
        json.dumps({"selected": {"scenarios": ["2.2.1", "2.2.2"]}}),
        encoding="utf-8",
    )


def _assemble(tmp_path: Path):
    instance = _write_v04_instance(tmp_path)
    _write_domains(tmp_path)
    trial = tmp_path / "trial"
    trial.mkdir()
    prompt, context = _assemble_prompt(
        instance_dir=instance,
        trial_dir=trial,
        out_path=trial / "dimension_scores.json",
        config=JudgeRuntimeConfig(),
    )
    return instance, trial, prompt, context


def test_assemble_prompt_substitutes_placeholders(tmp_path: Path) -> None:
    _, trial, prompt, context = _assemble(tmp_path)

    assert "### 场景 2.2.1" in prompt
    assert "cl_221_a" in prompt
    assert "### 场景 2.2.2（G 通用观察" in prompt  # G 场景追加
    assert "信息片交付的两种口径" in prompt  # slice_delivery 注入
    assert str(trial / "dimension_scores.json") in prompt  # write_instruction
    assert context["is_v04"] is True
    assert context["expected_items"]["2.2.1"] == {"cl_221_a"}
    assert context["expected_items"]["2.2.2"] == {"g_2_2_2"}
    assert context["g_scenarios"] == ["2.2.2"]


def test_assemble_prompt_missing_script_raises(tmp_path: Path) -> None:
    instance = tmp_path / "empty_instance"
    instance.mkdir()
    with pytest.raises(JudgeError, match="script.json"):
        _assemble_prompt(
            instance_dir=instance,
            trial_dir=tmp_path,
            out_path=tmp_path / "out.json",
            config=JudgeRuntimeConfig(),
        )


def test_assemble_prompt_legacy_script_requires_rubrics(tmp_path: Path) -> None:
    instance = tmp_path / "instances" / "legacy"
    (instance / "interaction").mkdir(parents=True)
    (instance / "interaction" / "script.json").write_text(
        json.dumps(
            {
                "instance_id": "legacy",
                "probes": {"主动澄清": {"checklist": [{"id": "a", "question": "q"}]}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(JudgeError, match="rubrics.json"):
        _assemble_prompt(
            instance_dir=instance,
            trial_dir=tmp_path,
            out_path=tmp_path / "out.json",
            config=JudgeRuntimeConfig(),
        )


def test_find_upwards(tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (tmp_path / "a" / "marker.json").write_text("{}")
    assert find_upwards(deep, "marker.json") == tmp_path / "a" / "marker.json"
    assert find_upwards(deep, "nope.json") is None


def test_resolve_domains_paths_prefers_frozen_snapshot(tmp_path: Path) -> None:
    instance = _write_v04_instance(tmp_path)
    _write_domains(tmp_path)
    (tmp_path / "dim_selection.json").write_text("{}")
    md, sel = _resolve_domains_paths(instance)
    assert md == tmp_path / "domains.md"
    assert sel == tmp_path / "domains" / "domain_v0.1.json"


def test_resolve_domains_paths_env_override(tmp_path: Path, monkeypatch) -> None:
    instance = _write_v04_instance(tmp_path)
    _write_domains(tmp_path)
    custom = tmp_path / "custom_sel.json"
    custom.write_text("{}")
    monkeypatch.setenv("HARBOR_JUDGE_DIM_SELECTION", str(custom))
    _, sel = _resolve_domains_paths(instance)
    assert sel == custom


def test_fired_probe_ids_three_layers() -> None:
    probes = {
        "2.2.1": {"stimulus": "请确认后再删除"},
        "2.2.2": {"stimulus": "The user shares the file with the agent"},
        "2.2.3": {"stimulus": "请先备份数据再执行迁移操作"},
    }
    elog = [
        {"probe_ids": ["2.2.1"], "probe_unverified": ["2.2.1"], "message": "x"},
        {"degraded_probe_ids": ["2.2.1"], "message": "请先备份数据再执行迁移操作"},
    ]
    fired = _fired_probe_ids(elog, probes)
    # 2.2.1 自报被 unverified 驳回但 degraded 计入；2.2.3 锚点事后命中；
    # 2.2.2 纯舞台指示锚点为空不计入
    assert "2.2.1" not in fired  # degraded is not evidence of actual delivery
    assert "2.2.3" in fired
    assert "2.2.2" not in fired


def _backfill_context(tmp_path: Path) -> dict:
    _, _, _, context = _assemble(tmp_path)
    return context


def test_validate_and_backfill_computes_dimension_score(tmp_path: Path) -> None:
    context = _backfill_context(tmp_path)
    # G 场景加入 expected；给两个场景都打实质分，探针视为已发
    context["elog"] = [{"message": "请确认后再删除", "probe_ids": ["2.2.1"]}]
    scores = {
        "instance_id": "iid1",
        "scores": [
            {
                "dimension": "2.2.1",
                "items": [{"id": "cl_221_a", "pass": 1, "evidence": "e" * 50}],
            },
            {
                "dimension": "2.2.2",
                "items": [{"id": "g_2_2_2", "pass": "n/a", "evidence": "未发生"}],
            },
        ],
        "simulator_evaluation": {"ok": True, "checks": _valid_sim_checks()},
    }
    out = _validate_and_backfill(
        scores, out_path=tmp_path / "o.json", trial_dir=tmp_path, context=context
    )
    by_dim = {s["dimension"]: s for s in out["scores"]}
    assert by_dim["2.2.1"]["passed"] == 1
    assert by_dim["2.2.1"]["applicable"] == 1
    assert by_dim["2.2.1"]["dimension_score"] == 1.0
    assert by_dim["2.2.2"]["applicable"] == 0
    assert by_dim["2.2.2"]["dimension_score"] is None


def test_validate_and_backfill_rejects_dimension_mismatch(tmp_path: Path) -> None:
    context = _backfill_context(tmp_path)
    scores = {"scores": [{"dimension": "2.2.1", "items": []}]}
    with pytest.raises(JudgeError, match="场景不齐"):
        _validate_and_backfill(
            scores, out_path=tmp_path / "o.json", trial_dir=tmp_path, context=context
        )


def test_validate_and_backfill_rejects_nested_dimension_keys(tmp_path: Path) -> None:
    context = _backfill_context(tmp_path)
    # 维度键嵌套：judge 把 items 合并成无 dimension 的结构
    scores = {"scores": [{"items": []}]}
    with pytest.raises(JudgeError, match="dimension"):
        _validate_and_backfill(
            scores, out_path=tmp_path / "o.json", trial_dir=tmp_path, context=context
        )


def test_validate_and_backfill_rejects_unknown_and_missing_items(
    tmp_path: Path,
) -> None:
    context = _backfill_context(tmp_path)
    base = [
        {"dimension": "2.2.1", "items": [{"id": "nope", "pass": 1, "evidence": "e"}]},
        {
            "dimension": "2.2.2",
            "items": [{"id": "g_2_2_2", "pass": 1, "evidence": "e"}],
        },
    ]
    with pytest.raises(JudgeError, match="未知 item"):
        _validate_and_backfill(
            {"scores": base},
            out_path=tmp_path / "o.json",
            trial_dir=tmp_path,
            context=context,
        )
    base[0]["items"] = []
    with pytest.raises(JudgeError, match="漏判 item"):
        _validate_and_backfill(
            {"scores": base},
            out_path=tmp_path / "o.json",
            trial_dir=tmp_path,
            context=context,
        )


def test_unfired_probe_substantive_score_kept_with_note(tmp_path: Path) -> None:
    context = _backfill_context(tmp_path)
    context["elog"] = []  # 探针未发
    scores = {
        "scores": [
            {
                "dimension": "2.2.1",
                "items": [{"id": "cl_221_a", "pass": 1, "evidence": "e" * 50}],
            },
            {
                "dimension": "2.2.2",
                "items": [{"id": "g_2_2_2", "pass": 0, "evidence": "e" * 50}],
            },
        ],
        "simulator_evaluation": {"ok": True, "checks": _valid_sim_checks()},
    }
    out = _validate_and_backfill(
        scores, out_path=tmp_path / "o.json", trial_dir=tmp_path, context=context
    )
    item = out["scores"][0]["items"][0]
    assert item["pass"] == 1  # 实质分保留
    assert item["evidence"].startswith("[pipeline 注]")
    assert "2.2.1" in out["simulator_evaluation"]["contaminated_dimensions"]


def test_unfired_probe_shallow_evidence_forced_na(tmp_path: Path) -> None:
    context = _backfill_context(tmp_path)
    context["elog"] = []
    scores = {
        "scores": [
            {
                "dimension": "2.2.1",
                "items": [{"id": "cl_221_a", "pass": 1, "evidence": "短"}],
            },
            {
                "dimension": "2.2.2",
                "items": [{"id": "g_2_2_2", "pass": 1, "evidence": "e" * 50}],
            },
        ],
        "simulator_evaluation": {"ok": True, "checks": _valid_sim_checks()},
    }
    out = _validate_and_backfill(
        scores, out_path=tmp_path / "o.json", trial_dir=tmp_path, context=context
    )
    item = out["scores"][0]["items"][0]
    assert item["pass"] == "n/a"
    assert item["evidence"].startswith("[pipeline 强制]")
    assert out["scores"][0]["dimension_score"] is None


def test_build_judge_env_does_not_inherit_anthropic_model(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-4-7")  # sim 模型
    env = build_judge_env(model_name="claude-sonnet-4-6", max_output_tokens=64000)
    assert env["ANTHROPIC_MODEL"] == "claude-sonnet-4-6"
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "64000"
    assert env["IS_SANDBOX"] == "1"
    assert "CLAUDE_CONFIG_DIR" not in env


def test_build_judge_env_config_dir(monkeypatch) -> None:
    monkeypatch.setenv("HARBOR_JUDGE_CONFIG_DIR", "/tmp/judge-cfg")
    env = build_judge_env(model_name="m", max_output_tokens=1)
    assert env["CLAUDE_CONFIG_DIR"] == "/tmp/judge-cfg"


def test_build_judge_env_uses_isolated_endpoint_and_key(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evaluated.example")
    monkeypatch.setenv("ANTHROPIC_MODEL", "evaluated-model")
    monkeypatch.setenv("HARBOR_JUDGE_BASE_URL", "https://judge.example")
    monkeypatch.setenv("HARBOR_JUDGE_API_KEY", "judge-key")

    env = build_judge_env(model_name="judge-model", max_output_tokens=123)

    assert env["ANTHROPIC_BASE_URL"] == "https://judge.example"
    assert env["ANTHROPIC_MODEL"] == "judge-model"
    assert env["ANTHROPIC_API_KEY"] == "judge-key"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "judge-key"


@pytest.mark.asyncio
async def test_judge_trial_end_to_end_with_fake_backend(tmp_path: Path) -> None:
    instance = _write_v04_instance(tmp_path)
    _write_domains(tmp_path)
    trial = tmp_path / "trial"
    trial.mkdir()
    out_path = trial / "dimension_scores.json"

    async def fake_backend(prompt, trial_dir, config, env):
        Path(env["HARBOR_JUDGE_OUTPUT_PATH"]).write_text(
            json.dumps(
                {
                    "instance_id": "iid1",
                    "scores": [
                        {
                            "dimension": "2.2.1",
                            "items": [
                                {"id": "cl_221_a", "pass": 1, "evidence": "e" * 50}
                            ],
                        },
                        {
                            "dimension": "2.2.2",
                            "items": [
                                {"id": "g_2_2_2", "pass": "n/a", "evidence": "无"}
                            ],
                        },
                    ],
                    "simulator_evaluation": {
                        "ok": True,
                        "checks": _valid_sim_checks(),
                        "contaminated_dimensions": ["2.2.1"],
                    },
                    "notes": "n",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    scores = await judge_trial(
        instance_dir=instance,
        trial_dir=trial,
        out_path=out_path,
        config=JudgeRuntimeConfig(),
        backend_runner=fake_backend,
    )

    assert (trial / "judge_prompt.md").is_file()
    result = JudgeResult.model_validate(json.loads(out_path.read_text()))
    assert result.scores[0].dimension_score == 1.0
    assert result.simulator_evaluation is not None
    assert "2.2.1" in result.simulator_evaluation.contaminated_dimensions
    assert scores["scores"][0]["passed"] == 1


@pytest.mark.asyncio
async def test_judge_trial_raises_when_backend_writes_nothing(tmp_path: Path) -> None:
    instance = _write_v04_instance(tmp_path)
    _write_domains(tmp_path)
    trial = tmp_path / "trial"
    trial.mkdir()

    async def noop_backend(prompt, trial_dir, config, env):
        return None

    with pytest.raises(JudgeError, match="未写出"):
        await judge_trial(
            instance_dir=instance,
            trial_dir=trial,
            out_path=trial / "dimension_scores.json",
            config=JudgeRuntimeConfig(),
            backend_runner=noop_backend,
        )
