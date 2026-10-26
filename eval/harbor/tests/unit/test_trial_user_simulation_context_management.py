import tempfile
import json
import os
from pathlib import Path
from unittest.mock import patch

from harbor.agents.base import BaseAgent
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.environments.base import BaseEnvironment, ExecResult
from harbor.models.agent.context import AgentContext
from harbor.models.environment_type import EnvironmentType
from harbor.models.trial.config import (
    AgentConfig,
    EnvironmentConfig,
    TaskConfig,
    TrialConfig,
    VerifierConfig,
)
from harbor.trial.trial import Trial


class RecoveringInteractiveAgent(BaseAgent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls: list[tuple[str, int, str]] = []
        self._failed_once = False

    @staticmethod
    def name() -> str:
        return "recovering-interactive"

    def version(self) -> str:
        return "1.0.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        pass

    async def run_interactive_turn(
        self,
        user_message: str,
        environment: BaseEnvironment,
        context: AgentContext,
        session_id: str,
        turn_idx: int,
    ) -> str:
        self.calls.append((session_id, turn_idx, user_message))

        if user_message.startswith("[Context Reset Recovery]"):
            return "Handoff acknowledged. Ready to continue."

        if not self._failed_once:
            self._failed_once = True
            raise NonZeroAgentExitCodeError(
                "Command failed (exit 1): maximum context length exceeded"
            )

        return f"Done: {user_message}"


class RecordingEnvironment(BaseEnvironment):
    @staticmethod
    def type() -> EnvironmentType:
        return EnvironmentType.DOCKER

    @property
    def is_mounted(self) -> bool:
        return True

    @property
    def supports_gpus(self) -> bool:
        return False

    @property
    def can_disable_internet(self) -> bool:
        return True

    def _validate_definition(self):
        pass

    async def start(self, force_build: bool) -> None:
        pass

    async def stop(self, delete: bool):
        pass

    async def upload_file(self, source_path, target_path):
        pass

    async def upload_dir(self, source_dir, target_dir):
        pass

    async def download_file(self, source_path, target_path):
        pass

    async def download_dir(self, source_dir, target_dir):
        pass

    async def exec(
        self, command, cwd=None, env=None, timeout_sec=None, user=None
    ) -> ExecResult:
        return ExecResult(stdout="", stderr="", return_code=0)


class StubUserSimulator:
    def __init__(self, *, config, system_prompt: str, original_instruction: str):
        self._messages = ["Please finish the report.", "#### STOP"]
        self._index = 0
        self._history: list[dict[str, str]] = []

    async def next_user_message(self) -> str:
        if self._index >= len(self._messages):
            msg = "#### STOP"
        else:
            msg = self._messages[self._index]
            self._index += 1
        self._history.append({"role": "user", "content": msg})
        return msg

    def receive_agent_message(self, content: str) -> None:
        self._history.append({"role": "assistant", "content": content})

    def should_stop(self, user_message: str) -> bool:
        return "#### STOP" in user_message

    def history(self) -> list[dict[str, str]]:
        return list(self._history)


class ConfigCapturingUserSimulator:
    captured_config: dict[str, object] | None = None

    def __init__(self, *, config, system_prompt: str, original_instruction: str):
        ConfigCapturingUserSimulator.captured_config = {
            "model_name": config.model_name,
            "temperature": config.temperature,
            "api_base": config.api_base,
            "api_key_set": bool(config.api_key),
            "max_tokens": config.max_tokens,
            "max_turns": config.max_turns,
            "max_history": config.max_history,
        }
        self._history: list[dict[str, str]] = []

    async def next_user_message(self) -> str:
        msg = "#### STOP"
        self._history.append({"role": "user", "content": msg})
        return msg

    def receive_agent_message(self, content: str) -> None:
        self._history.append({"role": "assistant", "content": content})

    def should_stop(self, user_message: str) -> bool:
        return "#### STOP" in user_message

    def history(self) -> list[dict[str, str]]:
        return list(self._history)


class ProactiveContextAgent(BaseAgent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls: list[tuple[str, int, str]] = []
        self._scheduled = False

    @staticmethod
    def name() -> str:
        return "proactive-context-agent"

    def version(self) -> str:
        return "1.0.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        pass

    async def run_interactive_turn(
        self,
        user_message: str,
        environment: BaseEnvironment,
        context: AgentContext,
        session_id: str,
        turn_idx: int,
    ) -> str:
        self.calls.append((session_id, turn_idx, user_message))

        if user_message.startswith("[Context Reset Recovery]"):
            return "Proactive handoff acknowledged."

        if not self._scheduled:
            self._scheduled = True
            actions_path = Path(self.logs_dir) / "context_actions.json"
            actions_path.write_text(
                json.dumps(
                    {
                        "actions": [
                            {
                                "tool": "manage_context",
                                "operation": "proactive_reset",
                                "method": "keep_recent_turns",
                                "value": 2,
                                "expected_delete_turns": 1,
                                "preserve_first_user_input": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            return "I have enough context; resetting proactively."

        return f"Done after proactive reset: {user_message}"


def _create_task_dir(root: Path) -> Path:
    task_dir = root / "test-task"
    task_dir.mkdir()

    (task_dir / "task.toml").write_text(
        "\n".join(
            [
                "[agent]",
                "timeout_sec = 30.0",
                "",
                "[verifier]",
                "timeout_sec = 10.0",
                "",
                "[environment]",
                "",
                "[user_simulation]",
                "enabled = true",
                "max_turns = 3",
                'system_prompt_path = "docs/user_system_prompt.md"',
                "",
                "[context_management]",
                "enabled = true",
                "auto_reset_on_overflow = true",
                "max_resets = 2",
                "summary_recent_turns = 4",
            ]
        )
    )
    (task_dir / "instruction.md").write_text("Complete the report task.")

    docs_dir = task_dir / "docs"
    docs_dir.mkdir()
    (docs_dir / "user_system_prompt.md").write_text(
        "Act as the end user and drive the task to completion."
    )

    env_dir = task_dir / "environment"
    env_dir.mkdir()
    (env_dir / "Dockerfile").write_text("FROM ubuntu:24.04\n")

    tests_dir = task_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "test.sh").write_text(
        "#!/bin/bash\necho 1 > /logs/verifier/reward.txt\n"
    )

    return task_dir


async def _make_trial(tmp_path: Path) -> Trial:
    task_dir = _create_task_dir(tmp_path)
    trials_dir = tmp_path / "trials"
    trials_dir.mkdir()

    config = TrialConfig(
        task=TaskConfig(path=task_dir),
        trials_dir=trials_dir,
        agent=AgentConfig(
            import_path="tests.unit.test_trial_user_simulation_context_management:RecoveringInteractiveAgent"
        ),
        environment=EnvironmentConfig(
            import_path="tests.unit.test_trial_user_simulation_context_management:RecordingEnvironment",
            delete=True,
        ),
        verifier=VerifierConfig(disable=True),
    )
    return await Trial.create(config)


class TestUserSimulationContextManagement:
    async def test_auto_reset_and_resume_on_context_overflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            trial = await _make_trial(Path(tmp))
            with patch("harbor.trial.trial.UserSimulator", StubUserSimulator):
                result = await trial.run()

            assert result.exception_info is None
            assert result.agent_result is not None
            assert result.agent_result.metadata is not None

            cm_meta = result.agent_result.metadata["context_management"]
            assert cm_meta["enabled"] is True
            assert cm_meta["reset_count"] == 1

            us_meta = result.agent_result.metadata["user_simulation"]
            assert us_meta["stopped_by_user"] is True
            assert any(
                item.get("role") == "system"
                and "[context_reset]" in str(item.get("content", ""))
                for item in us_meta["conversation"]
            )

            agent = trial.agent
            assert isinstance(agent, RecoveringInteractiveAgent)
            assert len(agent.calls) == 2
            assert agent.calls[0][0].endswith("-user-sim")
            assert agent.calls[1][0].endswith("-ctxreset-1")
            assert agent.calls[1][1] == 0
            assert "Please finish the report." in agent.calls[1][2]
            # 恢复请求已经携带未完成事项，不能再重复执行一轮相同请求。

    async def test_proactive_context_action_triggers_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = _create_task_dir(Path(tmp))
            trials_dir = Path(tmp) / "trials-proactive"
            trials_dir.mkdir()

            config = TrialConfig(
                task=TaskConfig(path=task_dir),
                trials_dir=trials_dir,
                agent=AgentConfig(
                    import_path="tests.unit.test_trial_user_simulation_context_management:ProactiveContextAgent"
                ),
                environment=EnvironmentConfig(
                    import_path="tests.unit.test_trial_user_simulation_context_management:RecordingEnvironment",
                    delete=True,
                ),
                verifier=VerifierConfig(disable=True),
            )
            trial = await Trial.create(config)

            with patch("harbor.trial.trial.UserSimulator", StubUserSimulator):
                result = await trial.run()

            assert result.exception_info is None
            assert result.agent_result is not None
            assert result.agent_result.metadata is not None

            us_meta = result.agent_result.metadata["user_simulation"]
            assert any(
                item.get("role") == "system"
                and "(proactive)" in str(item.get("content", ""))
                for item in us_meta["conversation"]
            )

            cm_meta = result.agent_result.metadata["context_management"]
            assert cm_meta["reset_count"] == 1

            agent = trial.agent
            assert isinstance(agent, ProactiveContextAgent)
            assert len(agent.calls) == 2
            assert agent.calls[0][0].endswith("-user-sim")
            assert agent.calls[1][0].endswith("-ctxreset-1")

    async def test_user_sim_model_is_overridable_at_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            trial = await _make_trial(Path(tmp))
            ConfigCapturingUserSimulator.captured_config = None
            with (
                patch("harbor.trial.trial.UserSimulator", ConfigCapturingUserSimulator),
                patch.dict(
                    os.environ,
                    {
                        "HARBOR_USER_SIM_MODEL_NAME": "deepseek-ai/DeepSeek-V3.2",
                        "HARBOR_USER_SIM_API_BASE": "https://example.test/v1",
                        "HARBOR_USER_SIM_API_KEY": "dummy-secret",
                        "HARBOR_USER_SIM_TEMPERATURE": "0.2",
                        "HARBOR_USER_SIM_MAX_TOKENS": "2048",
                        "HARBOR_USER_SIM_MAX_TURNS": "6",
                        "HARBOR_USER_SIM_MAX_HISTORY": "80",
                    },
                    clear=False,
                ),
            ):
                result = await trial.run()

            assert result.exception_info is None
            assert ConfigCapturingUserSimulator.captured_config is not None
            assert (
                ConfigCapturingUserSimulator.captured_config["model_name"]
                == "deepseek-ai/DeepSeek-V3.2"
            )
            assert ConfigCapturingUserSimulator.captured_config["temperature"] == 0.2
            assert (
                ConfigCapturingUserSimulator.captured_config["api_base"]
                == "https://example.test/v1"
            )
            assert ConfigCapturingUserSimulator.captured_config["api_key_set"] is True
            assert ConfigCapturingUserSimulator.captured_config["max_tokens"] == 2048
            assert ConfigCapturingUserSimulator.captured_config["max_turns"] == 6
            assert ConfigCapturingUserSimulator.captured_config["max_history"] == 80

            assert result.agent_result is not None
            assert result.agent_result.metadata is not None
            us_meta = result.agent_result.metadata["user_simulation"]
            assert (
                us_meta["effective_config"]["model_name"] == "deepseek-ai/DeepSeek-V3.2"
            )
            assert us_meta["effective_config"]["api_key_set"] is True
            assert (
                us_meta["runtime_overrides"]["applied"]["HARBOR_USER_SIM_MODEL_NAME"]
                == "deepseek-ai/DeepSeek-V3.2"
            )
