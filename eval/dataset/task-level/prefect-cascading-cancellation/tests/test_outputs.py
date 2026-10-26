# Behavior tests for cascading cancellation of in-process subflows.
#
# These tests boot a local Prefect server (sqlite), deploy flows that call
# nested in-process subflows, execute the top-level run in a subprocess via
# `prefect flow-run execute`, cancel the top-level run through the API, and
# assert the externally visible contract:
#   * the execute process exits cleanly (return code 0),
#   * the top-level run reaches Cancelled,
#   * the nested subflow run reaches Cancelled (not Crashed),
#   * the `on_cancellation` hooks of both parent and child actually run.
# A normal-completion scenario guards the untouched happy path.

import os
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

# The test process talks to the local server started by the fixture below.
# Point the Prefect SDK at it before importing prefect so no ephemeral
# per-process server is created instead.
SERVER_PORT = 4487
API_URL = "http://127.0.0.1:{0}/api".format(SERVER_PORT)
WORK_DIR = Path("/tmp/pcc-verify")

os.environ.setdefault("PREFECT_API_URL", API_URL)
os.environ.setdefault("PREFECT_HOME", str(WORK_DIR / "prefect-home"))
os.environ.setdefault("PREFECT_CLI_PROMPT", "false")
os.environ.setdefault("PREFECT_SERVER_ANALYTICS_ENABLED", "false")
os.environ.setdefault("PREFECT_ANALYTICS_ENABLED", "false")

import prefect  # noqa: E402
import pytest  # noqa: E402
from prefect import get_client  # noqa: E402
from prefect.client.schemas.actions import WorkPoolCreate  # noqa: E402
from prefect.client.schemas.filters import (  # noqa: E402
    FlowRunFilter,
    FlowRunFilterParentFlowRunId,
)
from prefect.client.schemas.sorting import FlowRunSort  # noqa: E402
from prefect.states import Cancelling  # noqa: E402

SERVER_START_TIMEOUT = 300
PARENT_RUNNING_TIMEOUT = 180
CHILD_RUNNING_TIMEOUT = 180
EXECUTE_EXIT_TIMEOUT = 150
TERMINAL_STATE_TIMEOUT = 90
COMMAND_TIMEOUT = 120

FLOWS_SOURCE = '''\
import asyncio
import time
from pathlib import Path

from prefect import flow


def _write_marker(marker_dir: str, name: str, flow_run) -> None:
    Path(marker_dir, name).write_text(str(flow_run.id))


def child_cancel_hook(flow, flow_run, state):
    _write_marker(flow_run.parameters["marker_dir"], "child-cancelled", flow_run)


def parent_cancel_hook(flow, flow_run, state):
    _write_marker(flow_run.parameters["marker_dir"], "parent-cancelled", flow_run)


@flow(on_cancellation=[child_cancel_hook], log_prints=True)
def child_flow(marker_dir: str):
    Path(marker_dir, "child-started").write_text("started")
    while True:
        time.sleep(0.2)


@flow(on_cancellation=[parent_cancel_hook], log_prints=True)
def parent_flow(marker_dir: str):
    child_flow(marker_dir)


@flow(on_cancellation=[child_cancel_hook], log_prints=True)
async def async_child_flow(marker_dir: str):
    Path(marker_dir, "child-started").write_text("started")
    while True:
        await asyncio.sleep(0.2)


@flow(on_cancellation=[parent_cancel_hook], log_prints=True)
async def async_parent_flow(marker_dir: str):
    await async_child_flow(marker_dir)


@flow(log_prints=True)
def quick_child():
    return 1


@flow(log_prints=True)
def quick_parent(marker_dir: str = ""):
    return quick_child()
'''


def _tail(path: Path, limit: int = 4000) -> str:
    if not path.exists():
        return "<no log at {0}>".format(path)
    return path.read_text(errors="replace")[-limit:]


def _wait_for(predicate, *, timeout: float, message: str, interval: float = 0.5):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            result = predicate()
        except Exception as exc:  # diagnostic path
            last_error = exc
            result = None
        if result:
            return result
        time.sleep(interval)
    if last_error is not None:
        raise AssertionError(message) from last_error
    raise AssertionError(message)


def _read_flow_run(flow_run_id):
    with get_client(sync_client=True) as client:
        return client.read_flow_run(flow_run_id)


def _read_child_flow_runs(parent_flow_run_id):
    with get_client(sync_client=True) as client:
        return client.read_flow_runs(
            flow_run_filter=FlowRunFilter(
                parent_flow_run_id=FlowRunFilterParentFlowRunId(
                    any_=[parent_flow_run_id]
                )
            ),
            sort=FlowRunSort.EXPECTED_START_TIME_ASC,
        )


@pytest.fixture(scope="session", autouse=True)
def prefect_server():
    """Start a local Prefect server (sqlite backend) once for all tests."""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    server_log_path = WORK_DIR / "server.log"
    logf = open(server_log_path, "w")
    proc = subprocess.Popen(
        [
            "prefect",
            "server",
            "start",
            "--host",
            "127.0.0.1",
            "--port",
            str(SERVER_PORT),
        ],
        stdout=logf,
        stderr=subprocess.STDOUT,
        env=dict(os.environ),
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + SERVER_START_TIMEOUT
        while True:
            if proc.poll() is not None:
                raise AssertionError(
                    "prefect server exited early; see {0}\n{1}".format(
                        server_log_path, _tail(server_log_path)
                    )
                )
            try:
                with urllib.request.urlopen(API_URL + "/health", timeout=3) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                pass
            if time.monotonic() > deadline:
                raise AssertionError(
                    "prefect server did not become healthy in time; see {0}\n{1}".format(
                        server_log_path, _tail(server_log_path)
                    )
                )
            time.sleep(2)
        yield API_URL
    finally:
        try:
            import signal as _signal

            os.killpg(proc.pid, _signal.SIGTERM)
        except Exception:
            proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            try:
                import signal as _signal

                os.killpg(proc.pid, _signal.SIGKILL)
            except Exception:
                proc.kill()
            proc.wait(timeout=30)
        logf.close()


def _prepare_deployment(scenario_dir: Path, entrypoint: str, marker_dir: Path):
    """Create a process work pool and deploy the flow from the scenario dir."""
    tag = uuid.uuid4().hex[:8]
    work_pool_name = "pcc-pool-{0}".format(tag)
    deployment_name = "pcc-deploy-{0}".format(tag)

    with get_client(sync_client=True) as client:
        client.create_work_pool(
            work_pool=WorkPoolCreate(name=work_pool_name, type="process")
        )

    deployment_id = prefect.flow.from_source(
        source=str(scenario_dir),
        entrypoint=entrypoint,
    ).deploy(
        name=deployment_name,
        work_pool_name=work_pool_name,
        parameters={"marker_dir": str(marker_dir)},
        build=False,
        push=False,
        print_next_steps=False,
        ignore_warnings=True,
    )
    return deployment_id


def _spawn_execute(flow_run_id, log_path: Path):
    logf = open(log_path, "w")
    proc = subprocess.Popen(
        ["prefect", "flow-run", "execute", str(flow_run_id)],
        stdout=logf,
        stderr=subprocess.STDOUT,
        env=dict(os.environ),
        start_new_session=True,
    )
    return proc, logf


def _stop_execute(proc, logf):
    if proc.poll() is None:
        try:
            import signal as _signal

            os.killpg(proc.pid, _signal.SIGKILL)
        except Exception:
            proc.kill()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
    logf.close()


def _run_cancellation_scenario(prefect_server, entrypoint: str):
    """Cancel a running top-level flow run and return the observed facts."""
    scenario_dir = WORK_DIR / ("scenario-" + uuid.uuid4().hex[:8])
    marker_dir = scenario_dir / "markers"
    marker_dir.mkdir(parents=True)
    (scenario_dir / "flows.py").write_text(FLOWS_SOURCE)
    execute_log_path = scenario_dir / "flow-run-execute.log"

    deployment_id = _prepare_deployment(scenario_dir, entrypoint, marker_dir)

    with get_client(sync_client=True) as client:
        parent_run = client.create_flow_run_from_deployment(deployment_id)
    parent_id = parent_run.id

    proc, logf = _spawn_execute(parent_id, execute_log_path)
    try:
        _wait_for(
            lambda: (
                run
                if (run := _read_flow_run(parent_id)).state
                and run.state.is_running()
                else None
            ),
            timeout=PARENT_RUNNING_TIMEOUT,
            message=(
                "Top-level flow run never entered RUNNING.\n"
                "Execution log:\n{0}".format(_tail(execute_log_path))
            ),
        )

        child_run = _wait_for(
            lambda: (
                running[0]
                if (
                    running := [
                        r
                        for r in _read_child_flow_runs(parent_id)
                        if r.state and r.state.is_running()
                    ]
                )
                else None
            ),
            timeout=CHILD_RUNNING_TIMEOUT,
            message=(
                "Nested subflow run never entered RUNNING.\n"
                "Execution log:\n{0}".format(_tail(execute_log_path))
            ),
        )

        with get_client(sync_client=True) as client:
            client.set_flow_run_state(parent_id, Cancelling())

        return_code = proc.wait(timeout=EXECUTE_EXIT_TIMEOUT)

        parent_terminal = _wait_for(
            lambda: (
                run
                if (run := _read_flow_run(parent_id)).state
                and run.state.is_final()
                else None
            ),
            timeout=TERMINAL_STATE_TIMEOUT,
            message=(
                "Top-level flow run never reached a terminal state.\n"
                "Execution log:\n{0}".format(_tail(execute_log_path))
            ),
        )
        child_terminal = _wait_for(
            lambda: (
                run
                if (run := _read_flow_run(child_run.id)).state
                and run.state.is_final()
                else None
            ),
            timeout=TERMINAL_STATE_TIMEOUT,
            message=(
                "Nested subflow run never reached a terminal state.\n"
                "Execution log:\n{0}".format(_tail(execute_log_path))
            ),
        )

        return {
            "return_code": return_code,
            "parent_id": parent_id,
            "child_id": child_run.id,
            "parent_state": parent_terminal.state,
            "child_state": child_terminal.state,
            "marker_dir": marker_dir,
            "execute_log_path": execute_log_path,
        }
    finally:
        _stop_execute(proc, logf)


def _assert_cascading_cancellation(result):
    log_tail = _tail(result["execute_log_path"])
    assert result["return_code"] == 0, (
        "`prefect flow-run execute` exited with code {0}.\n"
        "Execution log:\n{1}".format(result["return_code"], log_tail)
    )

    parent_state = result["parent_state"]
    assert parent_state is not None and parent_state.is_cancelled(), (
        "Top-level flow run ended in {0!r} instead of Cancelled.\n"
        "Execution log:\n{1}".format(parent_state, log_tail)
    )

    child_state = result["child_state"]
    assert child_state is not None and child_state.is_cancelled(), (
        "Nested subflow run ended in {0!r} instead of Cancelled; "
        "cancellation did not cascade into the process.\n"
        "Execution log:\n{1}".format(child_state, log_tail)
    )

    marker_dir = result["marker_dir"]
    parent_marker = marker_dir / "parent-cancelled"
    child_marker = marker_dir / "child-cancelled"
    assert parent_marker.exists(), (
        "Parent flow on_cancellation hook never ran.\n"
        "Execution log:\n{0}".format(log_tail)
    )
    assert child_marker.exists(), (
        "Child subflow on_cancellation hook never ran.\n"
        "Execution log:\n{0}".format(log_tail)
    )
    assert parent_marker.read_text() == str(result["parent_id"]), (
        "Parent on_cancellation hook observed the wrong flow run: {0}".format(
            parent_marker.read_text()
        )
    )
    assert child_marker.read_text() == str(result["child_id"]), (
        "Child on_cancellation hook observed the wrong flow run: {0}".format(
            child_marker.read_text()
        )
    )


def test_sync_subflow_cancellation_cascades(prefect_server):
    result = _run_cancellation_scenario(prefect_server, "flows.py:parent_flow")
    _assert_cascading_cancellation(result)


def test_async_subflow_cancellation_cascades(prefect_server):
    result = _run_cancellation_scenario(
        prefect_server, "flows.py:async_parent_flow"
    )
    _assert_cascading_cancellation(result)


def test_normal_completion_is_unchanged(prefect_server):
    """A flow with a nested subflow that finishes normally must still end
    Completed with a clean process exit."""
    scenario_dir = WORK_DIR / ("scenario-" + uuid.uuid4().hex[:8])
    marker_dir = scenario_dir / "markers"
    marker_dir.mkdir(parents=True)
    (scenario_dir / "flows.py").write_text(FLOWS_SOURCE)
    execute_log_path = scenario_dir / "flow-run-execute.log"

    deployment_id = _prepare_deployment(
        scenario_dir, "flows.py:quick_parent", marker_dir
    )
    with get_client(sync_client=True) as client:
        parent_run = client.create_flow_run_from_deployment(deployment_id)
    parent_id = parent_run.id

    proc, logf = _spawn_execute(parent_id, execute_log_path)
    try:
        return_code = proc.wait(timeout=EXECUTE_EXIT_TIMEOUT)
        assert return_code == 0, (
            "`prefect flow-run execute` exited with code {0}.\n"
            "Execution log:\n{1}".format(return_code, _tail(execute_log_path))
        )

        parent_terminal = _wait_for(
            lambda: (
                run
                if (run := _read_flow_run(parent_id)).state
                and run.state.is_final()
                else None
            ),
            timeout=TERMINAL_STATE_TIMEOUT,
            message="Completed flow run never reached a terminal state.\n"
            "Execution log:\n{0}".format(_tail(execute_log_path)),
        )
        assert parent_terminal.state is not None
        assert parent_terminal.state.is_completed(), (
            "Flow run ended in {0!r} instead of Completed.".format(
                parent_terminal.state
            )
        )

        child_runs = _wait_for(
            lambda: _read_child_flow_runs(parent_id) or None,
            timeout=TERMINAL_STATE_TIMEOUT,
            message="No nested subflow run was created for the quick flow.",
        )
        assert len(child_runs) == 1, child_runs
        child_state = child_runs[0].state
        assert child_state is not None and child_state.is_completed(), (
            "Nested subflow run ended in {0!r} instead of Completed.".format(
                child_state
            )
        )
    finally:
        _stop_execute(proc, logf)
