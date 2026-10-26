from harbor.trial.sim_agent_backend import ClaudeAgentSession


def test_simulator_endpoint_isolated_from_evaluated_agent(tmp_path):
    env = {
        "ANTHROPIC_BASE_URL": "https://evaluated.example",
        "ANTHROPIC_MODEL": "evaluated",
        "HARBOR_USER_SIM_ANTHROPIC_BASE_URL": "https://simulator.example",
        "HARBOR_USER_SIM_ANTHROPIC_MODEL": "simulator",
        "HARBOR_USER_SIM_ANTHROPIC_API_KEY": "sim-key",
        "HARBOR_USER_SIM_ANTHROPIC_AUTH_TOKEN": "sim-token",
    }
    session = ClaudeAgentSession(claude_bin="claude", workdir=tmp_path, env=env)
    assert session._env["ANTHROPIC_BASE_URL"] == "https://simulator.example"
    assert session._env["ANTHROPIC_MODEL"] == "simulator"
    assert session._env["ANTHROPIC_API_KEY"] == "sim-key"
    assert session._env["ANTHROPIC_AUTH_TOKEN"] == "sim-token"
    assert env["ANTHROPIC_MODEL"] == "evaluated"
    fallback = ClaudeAgentSession(
        claude_bin="claude", workdir=tmp_path, env={"ANTHROPIC_MODEL": "original"}
    )
    assert fallback._env["ANTHROPIC_MODEL"] == "original"


def test_simulator_agent_settings_configure_claude_cli(tmp_path):
    session = ClaudeAgentSession(
        claude_bin="claude",
        workdir=tmp_path,
        env={
            "ANTHROPIC_BASE_URL": "https://evaluated.example",
            "ANTHROPIC_MODEL": "evaluated",
            "HARBOR_USER_SIM_AGENT_BASE_URL": "https://simulator.example",
            "HARBOR_USER_SIM_AGENT_MODEL": "simulator",
            "HARBOR_USER_SIM_AGENT_API_KEY": "sim-key",
        },
    )

    assert session._env["ANTHROPIC_BASE_URL"] == "https://simulator.example"
    assert session._env["ANTHROPIC_MODEL"] == "simulator"
    assert session._env["ANTHROPIC_API_KEY"] == "sim-key"
    assert session._env["ANTHROPIC_AUTH_TOKEN"] == "sim-key"


def test_simulator_turn_timeout_uses_scoped_environment_variable(tmp_path):
    session = ClaudeAgentSession(
        claude_bin="claude",
        workdir=tmp_path,
        env={"HARBOR_USER_SIM_TURN_TIMEOUT_SEC": "1200"},
    )
    assert session._timeout == 1200.0


def test_simulator_turn_timeout_invalid_value_keeps_default(tmp_path):
    session = ClaudeAgentSession(
        claude_bin="claude",
        workdir=tmp_path,
        env={"HARBOR_USER_SIM_TURN_TIMEOUT_SEC": "0"},
    )
    assert session._timeout == 900.0
