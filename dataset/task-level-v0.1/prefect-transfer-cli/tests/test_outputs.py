# Behavior tests for the `prefect transfer` CLI.
#
# These tests boot two real Prefect servers (separate ports, separate SQLite
# databases), create one profile per backend, and drive the `prefect` CLI as
# a subprocess to assert the transfer contract end to end:
# argument/profile validation, empty-source handling, dependency-ordered
# migration of work pools / queues / flows / deployments / variables /
# concurrency limits, idempotent re-runs (existing resources are skipped),
# and failure visibility when the destination backend is unreachable.

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

import pytest

PREFECT_BIN = shutil.which("prefect") or "/usr/local/bin/prefect"

SERVER_START_TIMEOUT = 300
TRANSFER_TIMEOUT = 600
CLI_TIMEOUT = 180

SRC_PROFILE = "src"
DST_PROFILE = "dst"
BROKEN_PROFILE = "broken"

VAR_NAME = "transfer_var"
VAR_VALUE = "transfer-value-123"
VAR_TAGS = ["alpha", "beta"]
POOL_NAME = "transfer-pool"
POOL_TYPE = "process"
QUEUE_NAME = "transfer-queue"
GCL_NAME = "transfer-gcl"
GCL_LIMIT = 3
FLOW_NAME = "transfer-flow"
DEPLOYMENT_NAME = "transfer-deployment"


def _free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def _base_env():
    """Environment for subprocesses with any inherited Prefect settings
    stripped so the test fully controls configuration."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PREFECT_")
    }
    # dateparser/tzlocal choke on non-zoneinfo TZ values (e.g. "CST-8").
    env["TZ"] = "UTC"
    return env


def _http_json(method, url, body=None, timeout=20):
    """Perform an HTTP call and decode the JSON response (None when empty)."""
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as err:
        raw = err.read()
        raise AssertionError(
            "{0} {1} -> {2}: {3}".format(method, url, err.code, raw[:500])
        )
    if not raw:
        return None
    return json.loads(raw)


class Backend:
    """Two live Prefect servers plus a client home holding the profiles."""

    def __init__(self, work_dir, src_port, dst_port, broken_port):
        self.work_dir = work_dir
        self.src_port = src_port
        self.dst_port = dst_port
        self.broken_port = broken_port
        self.src_api = "http://127.0.0.1:{0}/api".format(src_port)
        self.dst_api = "http://127.0.0.1:{0}/api".format(dst_port)

        client_home = os.path.join(work_dir, "client-home")
        os.makedirs(client_home, exist_ok=True)
        with open(os.path.join(client_home, "profiles.toml"), "w") as f:
            f.write(
                "active = \"{0}\"\n\n"
                "[profiles.{0}]\n"
                "PREFECT_API_URL = \"{1}\"\n\n"
                "[profiles.{2}]\n"
                "PREFECT_API_URL = \"{3}\"\n\n"
                "[profiles.{4}]\n"
                "PREFECT_API_URL = \"http://127.0.0.1:{5}/api\"\n".format(
                    SRC_PROFILE,
                    self.src_api,
                    DST_PROFILE,
                    self.dst_api,
                    BROKEN_PROFILE,
                    broken_port,
                )
            )
        self.client_env = _base_env()
        self.client_env["PREFECT_HOME"] = client_home
        self.client_env["HOME"] = client_home
        # Keep rich from wrapping long lines when output is piped.
        self.client_env["COLUMNS"] = "200"

    def run_cli(self, *args, profile=None, timeout=CLI_TIMEOUT):
        cmd = [PREFECT_BIN]
        if profile:
            cmd += ["--profile", profile]
        cmd += list(args)
        return subprocess.run(
            cmd,
            env=self.client_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def filter_names(self, api_base, route, filter_key, name):
        """POST a name filter and return matching resource dicts."""
        result = _http_json(
            "POST",
            api_base + route,
            {filter_key: {"name": {"any_": [name]}}},
        )
        assert isinstance(result, list), (route, result)
        return result


@pytest.fixture(scope="session")
def backend():
    """Boot two Prefect servers and tear them down after the session."""
    assert os.path.isfile(PREFECT_BIN), "prefect CLI not found on PATH"
    work_dir = tempfile.mkdtemp(prefix="prefect-transfer-verify-")
    src_port = _free_port()
    dst_port = _free_port()
    broken_port = _free_port()  # never bound: connections are refused

    procs = []
    logs = []
    try:
        for tag, port in (("src", src_port), ("dst", dst_port)):
            home = os.path.join(work_dir, "home-" + tag)
            os.makedirs(home, exist_ok=True)
            env = _base_env()
            env["PREFECT_HOME"] = home
            env["HOME"] = home
            env["PREFECT_SERVER_ANALYTICS_ENABLED"] = "false"
            log_path = os.path.join(work_dir, "server-{0}.log".format(tag))
            logf = open(log_path, "w")
            proc = subprocess.Popen(
                [
                    PREFECT_BIN, "server", "start",
                    "--host", "127.0.0.1",
                    "--port", str(port),
                    "--no-services",
                ],
                env=env,
                stdout=logf,
                stderr=subprocess.STDOUT,
            )
            procs.append(proc)
            logs.append(log_path)

            deadline = time.monotonic() + SERVER_START_TIMEOUT
            while True:
                if proc.poll() is not None:
                    raise AssertionError(
                        "prefect server ({0}) exited early; see {1}".format(
                            tag, log_path
                        )
                    )
                try:
                    with urllib.request.urlopen(
                        "http://127.0.0.1:{0}/api/health".format(port),
                        timeout=3,
                    ) as resp:
                        if resp.status == 200:
                            break
                except Exception:
                    pass
                if time.monotonic() > deadline:
                    raise AssertionError(
                        "prefect server ({0}) did not become healthy; "
                        "see {1}".format(tag, log_path)
                    )
                time.sleep(2)

        yield Backend(work_dir, src_port, dst_port, broken_port)
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=30)


@pytest.fixture(scope="session")
def seeded_source(backend):
    """Seed the source backend with a representative resource set:
    a variable (value + tags), a work pool, a non-default work queue in
    that pool, a global concurrency limit, and a deployment (with its
    flow) attached to the pool and queue."""
    proc = backend.run_cli(
        "variable", "set", VAR_NAME, VAR_VALUE,
        "--tag", VAR_TAGS[0], "--tag", VAR_TAGS[1],
        profile=SRC_PROFILE,
    )
    assert proc.returncode == 0, (proc.stdout, proc.stderr)

    proc = backend.run_cli(
        "work-pool", "create", POOL_NAME, "--type", POOL_TYPE,
        profile=SRC_PROFILE,
    )
    assert proc.returncode == 0, (proc.stdout, proc.stderr)

    proc = backend.run_cli(
        "work-queue", "create", QUEUE_NAME, "--pool", POOL_NAME,
        profile=SRC_PROFILE,
    )
    assert proc.returncode == 0, (proc.stdout, proc.stderr)

    proc = backend.run_cli(
        "global-concurrency-limit", "create", GCL_NAME,
        "--limit", str(GCL_LIMIT),
        profile=SRC_PROFILE,
    )
    assert proc.returncode == 0, (proc.stdout, proc.stderr)

    flow = _http_json("POST", backend.src_api + "/flows/", {"name": FLOW_NAME})
    assert flow.get("id"), flow

    _http_json(
        "POST",
        backend.src_api + "/deployments/",
        {
            "flow_id": flow["id"],
            "name": DEPLOYMENT_NAME,
            "entrypoint": "flows/example.py:main",
            "work_pool_name": POOL_NAME,
            "work_queue_name": QUEUE_NAME,
        },
    )
    return backend


def run_transfer(backend, timeout=TRANSFER_TIMEOUT):
    return backend.run_cli(
        "transfer", "--from", SRC_PROFILE, "--to", DST_PROFILE,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Command surface
# ---------------------------------------------------------------------------

def test_transfer_help(backend):
    proc = backend.run_cli("transfer", "--help")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    output = proc.stdout + proc.stderr
    assert "Transfer resources from one Prefect profile to another" in output, output
    assert "--from" in output, output
    assert "--to" in output, output


def test_transfer_requires_from_and_to_options(backend):
    proc = backend.run_cli("transfer")
    assert proc.returncode != 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "--from" in (proc.stdout + proc.stderr), (proc.stdout, proc.stderr)


# ---------------------------------------------------------------------------
# Profile validation (must not touch either backend)
# ---------------------------------------------------------------------------

def test_transfer_rejects_unknown_source_profile(backend):
    proc = backend.run_cli(
        "transfer", "--from", "ghost", "--to", DST_PROFILE,
    )
    assert proc.returncode != 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "Source profile 'ghost' not found" in (proc.stdout + proc.stderr), (
        proc.stdout,
        proc.stderr,
    )


def test_transfer_rejects_unknown_target_profile(backend):
    proc = backend.run_cli(
        "transfer", "--from", SRC_PROFILE, "--to", "ghost",
    )
    assert proc.returncode != 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "Target profile 'ghost' not found" in (proc.stdout + proc.stderr), (
        proc.stdout,
        proc.stderr,
    )


def test_transfer_rejects_identical_source_and_target(backend):
    proc = backend.run_cli(
        "transfer", "--from", SRC_PROFILE, "--to", SRC_PROFILE,
    )
    assert proc.returncode != 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "Source and target profiles must be different" in (
        proc.stdout + proc.stderr
    ), (proc.stdout, proc.stderr)


# ---------------------------------------------------------------------------
# Empty source (runs before any seeding happens)
# ---------------------------------------------------------------------------

def test_transfer_with_empty_source(backend):
    proc = run_transfer(backend)
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "No resources found to transfer" in (proc.stdout + proc.stderr), (
        proc.stdout,
        proc.stderr,
    )


# ---------------------------------------------------------------------------
# End-to-end transfer
# ---------------------------------------------------------------------------

def test_transfer_end_to_end(seeded_source):
    backend = seeded_source

    proc = run_transfer(backend)
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, (proc.returncode, output)
    # Results are summarized for the user at the end of the run.
    assert "succeeded" in output, output
    assert "with errors" not in output, output

    # The variable lands on the destination with value and tags intact,
    # exactly once.
    variables = backend.filter_names(
        backend.dst_api, "/variables/filter", "variables", VAR_NAME
    )
    assert len(variables) == 1, variables
    assert variables[0]["value"] == VAR_VALUE, variables
    for tag in VAR_TAGS:
        assert tag in variables[0].get("tags", []), variables

    # The work pool keeps its type.
    pools = backend.filter_names(
        backend.dst_api, "/work_pools/filter", "work_pools", POOL_NAME
    )
    assert len(pools) == 1, pools
    assert pools[0]["type"] == POOL_TYPE, pools
    dst_pool_id = pools[0]["id"]

    # The non-default queue exists on the destination, wired to the
    # destination pool (so the pool must have been transferred first).
    queues = backend.filter_names(
        backend.dst_api, "/work_queues/filter", "work_queues", QUEUE_NAME
    )
    assert len(queues) == 1, queues
    assert queues[0].get("work_pool_id") == dst_pool_id, (queues, pools)

    # The global concurrency limit keeps its limit.
    gcl = _http_json(
        "GET", backend.dst_api + "/v2/concurrency_limits/" + GCL_NAME
    )
    assert gcl["limit"] == GCL_LIMIT, gcl

    # The flow and its deployment are transferred, with the deployment
    # relinked to the destination-side flow.
    src_flows = backend.filter_names(
        backend.src_api, "/flows/filter", "flows", FLOW_NAME
    )
    dst_flows = backend.filter_names(
        backend.dst_api, "/flows/filter", "flows", FLOW_NAME
    )
    assert len(dst_flows) == 1, dst_flows
    deployments = backend.filter_names(
        backend.dst_api, "/deployments/filter", "deployments", DEPLOYMENT_NAME
    )
    assert len(deployments) == 1, deployments
    assert deployments[0]["flow_id"] == dst_flows[0]["id"], (
        deployments,
        dst_flows,
    )
    assert deployments[0]["flow_id"] != src_flows[0]["id"], (
        deployments,
        src_flows,
    )

    # Existing CLI commands keep working against both backends.
    proc = backend.run_cli("work-pool", "ls", profile=DST_PROFILE)
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert POOL_NAME in proc.stdout, proc.stdout

    proc = backend.run_cli("variable", "inspect", VAR_NAME, profile=DST_PROFILE)
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert VAR_VALUE in proc.stdout, proc.stdout


def test_transfer_is_idempotent(seeded_source):
    backend = seeded_source

    first = run_transfer(backend)
    assert first.returncode == 0, (first.returncode, first.stdout, first.stderr)

    second = run_transfer(backend)
    output = second.stdout + second.stderr
    assert second.returncode == 0, (second.returncode, output)
    # Already-migrated resources are reported as skipped, never as errors.
    assert "skipped" in output, output
    assert "with errors" not in output, output

    # Re-running must not duplicate resources on the destination.
    variables = backend.filter_names(
        backend.dst_api, "/variables/filter", "variables", VAR_NAME
    )
    assert len(variables) == 1, variables
    pools = backend.filter_names(
        backend.dst_api, "/work_pools/filter", "work_pools", POOL_NAME
    )
    assert len(pools) == 1, pools
    queues = backend.filter_names(
        backend.dst_api, "/work_queues/filter", "work_queues", QUEUE_NAME
    )
    assert len(queues) == 1, queues


# ---------------------------------------------------------------------------
# Failure visibility
# ---------------------------------------------------------------------------

def test_transfer_fails_when_destination_unreachable(seeded_source):
    backend = seeded_source
    proc = backend.run_cli(
        "transfer", "--from", SRC_PROFILE, "--to", BROKEN_PROFILE,
        timeout=TRANSFER_TIMEOUT,
    )
    output = proc.stdout + proc.stderr
    # The destination backend refuses every connection: the transfer must
    # surface the failure instead of pretending to succeed.
    assert proc.returncode != 0, (proc.returncode, output)
    assert "No such command" not in output, output
