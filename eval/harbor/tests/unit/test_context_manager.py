import json
from pathlib import Path

from harbor.models.task.config import ContextManagementConfig
from harbor.trial.context_manager import ContextManager, is_context_overflow_error


def _write_async_success_record(
    path: Path, *, session_id: str, user_message: str, agent_message: str
) -> None:
    payload = {
        "event": "async_success",
        "kwargs": {
            "metadata": {"user_id": f"user_hash_account__session_{session_id}"},
            "standard_logging_object": {
                "messages": [
                    {"role": "system", "content": "system prompt"},
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": user_message}],
                    },
                ],
                "response": {
                    "choices": [
                        {
                            "message": {
                                "content": agent_message,
                            }
                        }
                    ]
                },
            },
        },
    }
    path.write_text(path.read_text() + json.dumps(payload) + "\n")


def test_context_manager_detects_overflow_from_error_message():
    manager = ContextManager(
        config=ContextManagementConfig(enabled=True),
        original_instruction="Finish the task",
    )

    err = RuntimeError("Command failed: maximum context length exceeded")
    assert manager.is_context_overflow_error(err)
    assert manager.can_auto_reset(err)


def test_context_overflow_detector_recognizes_provider_exception_name():
    class ContextWindowExceededError(RuntimeError):
        pass

    assert is_context_overflow_error(ContextWindowExceededError("request rejected"))


def test_context_manager_respects_max_resets():
    manager = ContextManager(
        config=ContextManagementConfig(enabled=True, max_resets=1),
        original_instruction="Finish the task",
    )

    err = RuntimeError("context window exceeded")
    assert manager.can_auto_reset(err)

    new_session = manager.register_reset(
        base_session_id="trial-1-user-sim",
        previous_session_id="trial-1-user-sim",
        reason=str(err),
    )
    assert new_session == "trial-1-user-sim-ctxreset-1"
    assert not manager.can_auto_reset(err)


def test_context_manager_builds_handoff_from_litellm_logs(tmp_path, monkeypatch):
    log_path = tmp_path / "litellm.test.jsonl"
    log_path.write_text("")
    monkeypatch.setenv("HARBOR_LITELLM_JSONL_PATH", str(log_path))

    sid = "123e4567-e89b-12d3-a456-426614174000"
    _write_async_success_record(
        log_path,
        session_id=sid,
        user_message="Please gather this week's data",
        agent_message="Data gathered and normalized.",
    )
    _write_async_success_record(
        log_path,
        session_id=sid,
        user_message="Now generate summary charts",
        agent_message="Generated charts in /tmp/report/charts.",
    )

    # Different session should not leak into current session's handoff.
    _write_async_success_record(
        log_path,
        session_id="00000000-0000-0000-0000-000000000001",
        user_message="Other session message",
        agent_message="Other session response",
    )

    manager = ContextManager(
        config=ContextManagementConfig(
            enabled=True,
            summary_recent_turns=2,
            litellm_tail_bytes=1024 * 1024,
        ),
        original_instruction="Build a weekly report",
    )

    handoff = manager.build_reset_message(
        latest_user_message="Please finalize and export the report.",
        session_id=sid,
    )

    assert "Original task:\nBuild a weekly report" in handoff
    assert "Turn 1 User: Please gather this week's data" in handoff
    assert "Turn 2 Agent: Generated charts in /tmp/report/charts." in handoff
    assert "Other session response" not in handoff
    assert "Latest unfinished user request:" in handoff


def test_context_manager_returns_none_history_when_log_missing(tmp_path, monkeypatch):
    missing = tmp_path / "missing.jsonl"
    monkeypatch.setenv("HARBOR_LITELLM_JSONL_PATH", str(missing))

    manager = ContextManager(
        config=ContextManagementConfig(enabled=True),
        original_instruction="Task",
    )
    handoff = manager.build_reset_message(
        latest_user_message="continue",
        session_id="123e4567-e89b-12d3-a456-426614174000",
    )
    assert "No matching turns found in LiteLLM log source" in handoff


def test_context_manager_infers_litellm_path_from_run_yaml(tmp_path, monkeypatch):
    monkeypatch.delenv("HARBOR_LITELLM_JSONL_PATH", raising=False)

    run_dir = tmp_path / "runs" / "demo-run"
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True)
    log_path = logs_dir / "litellm.job-a.jsonl"
    log_path.write_text("")

    sid = "123e4567-e89b-12d3-a456-426614174000"
    _write_async_success_record(
        log_path,
        session_id=sid,
        user_message="First question",
        agent_message="First answer",
    )

    run_yaml = run_dir / "run.yaml"
    run_yaml.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "run:",
                "  run_id: demo-run",
                f"  output_root: {run_dir}",
                "jobs:",
                "  - name: job-a",
                "    litellm:",
                "      enabled: true",
                f"      log_jsonl_path: {log_path}",
                "  - name: job-b",
                "    litellm:",
                "      enabled: true",
                f"      log_jsonl_path: {logs_dir / 'litellm.job-b.jsonl'}",
            ]
        )
        + "\n"
    )

    trials_dir = run_dir / "jobs" / "job-a" / "2026-04-20__15-01-08"
    trial_dir = trials_dir / "task__abc123"
    trial_dir.mkdir(parents=True)

    manager = ContextManager(
        config=ContextManagementConfig(enabled=True, summary_recent_turns=2),
        original_instruction="Task",
        trial_dir=trial_dir,
        trials_dir=trials_dir,
    )

    handoff = manager.build_reset_message(
        latest_user_message="continue",
        session_id=sid,
    )

    assert "Turn 1 User: First question" in handoff
    assert "Turn 1 Agent: First answer" in handoff
    metadata = manager.to_metadata()
    assert metadata["litellm_log_path"] == str(log_path)


def test_invalid_history_recovery_has_a_budget_without_enabling_overflow_reset():
    from harbor.agents.installed.base import NonZeroAgentExitCodeError

    manager = ContextManager(
        config=ContextManagementConfig(enabled=False, max_resets=1),
        original_instruction="hidden specification",
    )
    error = NonZeroAgentExitCodeError(
        "Malformed conversation history: message[19].content is required"
    )
    assert manager.can_auto_reset(error)
    manager.register_reset(
        base_session_id="base", previous_session_id="old", reason=str(error)
    )
    assert not manager.can_auto_reset(error)


def test_interactive_recovery_does_not_leak_unreleased_instruction():
    manager = ContextManager(
        config=ContextManagementConfig(),
        original_instruction="SECRET_UNRELEASED_REQUIREMENT",
    )
    history = [
        {"role": "user", "content": "visible opening"},
        {"role": "assistant", "content": "completed first step"},
        {"role": "user", "content": "visible follow-up"},
    ]
    result = manager.build_reset_message(
        latest_user_message="visible follow-up",
        session_id="old",
        visible_history=history,
    )
    assert "SECRET_UNRELEASED_REQUIREMENT" not in result
    assert result.index("completed first step") < result.index(
        "user: visible follow-up"
    )
