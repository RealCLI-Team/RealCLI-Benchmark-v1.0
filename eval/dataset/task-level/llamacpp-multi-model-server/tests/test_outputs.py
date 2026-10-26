# Behavior tests for the llama-server multi-model router mode.
#
# These tests build llama-server from /app (incrementally, reusing the
# pre-warmed build tree), start it in router mode against a directory of
# tiny GGUF models baked into the image, and assert the public multi-model
# contract over HTTP: model listing/status, load/unload lifecycle with its
# error semantics, request routing by model name, autoload controls, and
# the --models-max LRU eviction.

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

APP_DIR = "/app"
BUILD_DIR = os.path.join(APP_DIR, "build")
SERVER_BIN = os.path.join(BUILD_DIR, "bin", "llama-server")
MODELS_DIR_SRC = "/opt/models"
WORK_DIR = "/tmp/router-verify"
MODELS_DIR = os.path.join(WORK_DIR, "models")
ROUTER_LOG_DIR = os.path.join(WORK_DIR, "logs")

BUILD_TIMEOUT = 1500
ROUTER_START_TIMEOUT = 90
MODEL_LOAD_TIMEOUT = 120

MODEL_A = "tiny-a"  # /opt/models/tiny-a.gguf
MODEL_B = "tiny-b"  # /opt/models/tiny-b.gguf
MODEL_C = "tiny-c"  # /opt/models/tiny-c/tiny-c.gguf (subdirectory layout)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def http_request(port, method, path, body=None, timeout=30):
    """HTTP call against a router; returns (status, decoded json or text)."""
    url = "http://127.0.0.1:{0}{1}".format(port, path)
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as err:
        raw = err.read()
        status = err.code
    if not raw:
        return status, None
    try:
        return status, json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return status, raw.decode(errors="replace")


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def list_models(port):
    status, body = http_request(port, "GET", "/v1/models", timeout=15)
    assert status == 200, (status, body)
    assert body.get("object") == "list", body
    return {m["id"]: m for m in body.get("data", [])}


def model_status(port, name):
    return list_models(port).get(name, {}).get("status", {})


def wait_model_status(port, name, value, timeout=MODEL_LOAD_TIMEOUT):
    """Poll /v1/models until the model reaches the expected status value."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        st = model_status(port, name)
        last = st
        if st.get("failed"):
            raise AssertionError(
                "model {0} failed to load: {1}".format(name, st))
        if st.get("value") == value:
            return st
        time.sleep(1)
    raise AssertionError(
        "model {0} did not reach status {1!r} in time; last: {2}".format(
            name, value, last))


def load_model(port, name):
    status, body = http_request(port, "POST", "/models/load",
                                {"model": name}, timeout=30)
    assert status == 200 and (body or {}).get("success") is True, (status, body)
    return wait_model_status(port, name, "loaded")


def unload_model(port, name):
    status, body = http_request(port, "POST", "/models/unload",
                                {"model": name}, timeout=30)
    assert status == 200 and (body or {}).get("success") is True, (status, body)
    wait_model_status(port, name, "unloaded")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def build_server():
    """Build llama-server from /app once (incremental on the warm tree)."""
    os.makedirs(WORK_DIR, exist_ok=True)
    build_log = os.path.join(WORK_DIR, "build.log")
    if not os.path.isdir(BUILD_DIR):
        with open(build_log, "w") as logf:
            proc = subprocess.run(
                ["cmake", "-S", APP_DIR, "-B", BUILD_DIR,
                 "-DCMAKE_BUILD_TYPE=Release", "-DGGML_NATIVE=OFF",
                 "-DLLAMA_BUILD_EXAMPLES=OFF", "-DLLAMA_BUILD_TESTS=OFF"],
                stdout=logf, stderr=subprocess.STDOUT, timeout=BUILD_TIMEOUT)
            assert proc.returncode == 0, \
                "cmake configure failed; see {0}".format(build_log)
    jobs = str(min(os.cpu_count() or 4, 32))
    with open(build_log, "a") as logf:
        proc = subprocess.run(
            ["cmake", "--build", BUILD_DIR, "--target", "llama-server",
             "-j", jobs],
            stdout=logf, stderr=subprocess.STDOUT, timeout=BUILD_TIMEOUT)
    assert proc.returncode == 0, \
        "llama-server build failed; see {0}".format(build_log)
    assert os.path.isfile(SERVER_BIN), "llama-server binary missing"

    # private copy of the model zoo so tests never mutate the baked-in one
    if os.path.isdir(MODELS_DIR):
        shutil.rmtree(MODELS_DIR)
    shutil.copytree(MODELS_DIR_SRC, MODELS_DIR)
    yield SERVER_BIN


class Router(object):
    def __init__(self, proc, port, log_path):
        self.proc = proc
        self.port = port
        self.log_path = log_path

    def url(self, path):
        return "http://127.0.0.1:{0}{1}".format(self.port, path)


@pytest.fixture
def router(build_server):
    """Start a router-mode llama-server per test and tear it down after."""
    started = []

    def start(extra_args=None):
        port = free_port()
        os.makedirs(ROUTER_LOG_DIR, exist_ok=True)
        log_path = os.path.join(ROUTER_LOG_DIR, "router-{0}.log".format(port))
        logf = open(log_path, "w")
        args = [SERVER_BIN, "--models-dir", MODELS_DIR,
                "--host", "127.0.0.1", "--port", str(port)]
        if extra_args:
            args += list(extra_args)
        proc = subprocess.Popen(args, stdout=logf, stderr=subprocess.STDOUT)
        started.append((proc, logf))
        try:
            deadline = time.monotonic() + ROUTER_START_TIMEOUT
            while True:
                if proc.poll() is not None:
                    raise AssertionError(
                        "router exited early with code {0}; see {1}".format(
                            proc.returncode, log_path))
                try:
                    status, _ = http_request(port, "GET", "/v1/models",
                                             timeout=3)
                    if status == 200:
                        break
                except Exception:
                    pass
                if time.monotonic() > deadline:
                    raise AssertionError(
                        "router did not start in time; see {0}".format(log_path))
                time.sleep(1)
        except Exception:
            logf.close()
            raise
        return Router(proc, port, log_path)

    yield start

    for proc, logf in started:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=20)
        logf.close()
    # any model instance child that outlived its router must not linger
    subprocess.run(["pkill", "-x", "llama-server"], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------------------
# model listing
# ---------------------------------------------------------------------------

def test_router_lists_models_from_models_dir(router):
    r = router()
    models = list_models(r.port)
    assert set(models) == {MODEL_A, MODEL_B, MODEL_C}, sorted(models)
    for name, rel in [(MODEL_A, "tiny-a.gguf"),
                      (MODEL_B, "tiny-b.gguf"),
                      (MODEL_C, os.path.join("tiny-c", "tiny-c.gguf"))]:
        entry = models[name]
        assert entry.get("in_cache") is False, entry
        assert entry.get("path", "").replace("\\", "/").endswith(rel), entry
        assert entry.get("status", {}).get("value") == "unloaded", entry

    # the OAI-incompatible alias endpoint exposes the same listing
    status, body = http_request(r.port, "GET", "/models", timeout=15)
    assert status == 200, (status, body)
    ids = {m.get("id") for m in body.get("data", [])}
    assert {MODEL_A, MODEL_B, MODEL_C} <= ids, body


def test_router_props_reports_router_role(router):
    r = router()
    status, body = http_request(r.port, "GET", "/props", timeout=15)
    assert status == 200, (status, body)
    assert body.get("role") == "router", body


# ---------------------------------------------------------------------------
# load / unload lifecycle
# ---------------------------------------------------------------------------

def test_load_then_unload_model(router):
    r = router()
    load_model(r.port, MODEL_A)
    st = model_status(r.port, MODEL_A)
    assert st.get("value") == "loaded", st
    assert isinstance(st.get("args"), list) and st["args"], st

    unload_model(r.port, MODEL_A)
    assert model_status(r.port, MODEL_A).get("value") == "unloaded"


def test_load_unknown_model_returns_404(router):
    r = router()
    status, body = http_request(r.port, "POST", "/models/load",
                                {"model": "no-such-model"}, timeout=15)
    assert status == 404, (status, body)
    assert (body or {}).get("error", {}).get("type") == "not_found_error", body


def test_load_unload_error_semantics(router):
    r = router()
    load_model(r.port, MODEL_A)

    # loading an already loaded model is a client error
    status, body = http_request(r.port, "POST", "/models/load",
                                {"model": MODEL_A}, timeout=15)
    assert status == 400, (status, body)

    unload_model(r.port, MODEL_A)

    # unloading a model that is not loaded is a client error
    status, body = http_request(r.port, "POST", "/models/unload",
                                {"model": MODEL_A}, timeout=15)
    assert status == 400, (status, body)

    # unloading an unknown model is a client error
    status, body = http_request(r.port, "POST", "/models/unload",
                                {"model": "no-such-model"}, timeout=15)
    assert status == 400, (status, body)


# ---------------------------------------------------------------------------
# request routing
# ---------------------------------------------------------------------------

def test_completion_routed_and_autoloads_model(router):
    r = router()
    payload = {"model": MODEL_A, "prompt": "Hello world",
               "n_predict": 16, "temperature": 0, "ignore_eos": True}
    status, body = http_request(r.port, "POST", "/v1/completions",
                                payload, timeout=MODEL_LOAD_TIMEOUT + 30)
    assert status == 200, (status, body)
    choices = (body or {}).get("choices") or []
    assert choices, body
    text = choices[0].get("text")
    assert isinstance(text, str) and text.strip(), body
    assert choices[0].get("finish_reason") == "length", body

    # the request auto-loaded the model on demand
    assert model_status(r.port, MODEL_A).get("value") == "loaded"


def test_chat_completion_routed_by_model_field(router):
    r = router()
    payload = {"model": MODEL_B,
               "messages": [{"role": "user", "content": "hi"}],
               "max_tokens": 8, "temperature": 0}
    status, body = http_request(r.port, "POST", "/v1/chat/completions",
                                payload, timeout=MODEL_LOAD_TIMEOUT + 30)
    assert status == 200, (status, body)
    choices = (body or {}).get("choices") or []
    assert choices, body
    message = choices[0].get("message") or {}
    assert message.get("role") == "assistant", body
    assert isinstance(message.get("content"), str) and message["content"], body


def test_routing_rejects_missing_or_unknown_model(router):
    r = router()
    # no "model" field in the POST body
    status, body = http_request(r.port, "POST", "/v1/completions",
                                {"prompt": "hi", "n_predict": 4}, timeout=15)
    assert status == 400, (status, body)

    # unknown model name
    status, body = http_request(r.port, "POST", "/v1/completions",
                                {"model": "no-such-model", "prompt": "hi",
                                 "n_predict": 4}, timeout=15)
    assert status == 400, (status, body)


def test_get_endpoint_routed_by_query_param(router):
    r = router()
    load_model(r.port, MODEL_A)
    path = "/props?model=" + urllib.parse.quote(MODEL_A)
    status, body = http_request(r.port, "GET", path, timeout=30)
    assert status == 200, (status, body)
    # the response comes from the model instance itself (its alias)
    assert body.get("model_alias") == MODEL_A, body


# ---------------------------------------------------------------------------
# autoload controls
# ---------------------------------------------------------------------------

def test_autoload_disabled_via_flag(router):
    r = router(["--no-models-autoload"])
    payload = {"model": MODEL_A, "prompt": "hi", "n_predict": 4}
    status, body = http_request(r.port, "POST", "/v1/completions",
                                payload, timeout=30)
    assert status == 400, (status, body)

    # explicit load makes the very same request succeed
    load_model(r.port, MODEL_A)
    status, body = http_request(r.port, "POST", "/v1/completions",
                                payload, timeout=30)
    assert status == 200, (status, body)
    assert (body or {}).get("choices"), body


def test_autoload_disabled_via_query_param(router):
    r = router()
    payload = {"model": MODEL_A, "prompt": "hi", "n_predict": 4}
    status, body = http_request(r.port, "POST",
                                "/v1/completions?autoload=false",
                                payload, timeout=30)
    assert status == 400, (status, body)
    # the model must still be unloaded afterwards
    assert model_status(r.port, MODEL_A).get("value") == "unloaded"

    # default behavior (no override) auto-loads
    status, body = http_request(r.port, "POST", "/v1/completions",
                                payload, timeout=MODEL_LOAD_TIMEOUT + 30)
    assert status == 200, (status, body)


# ---------------------------------------------------------------------------
# --models-max LRU eviction
# ---------------------------------------------------------------------------

def test_models_max_evicts_least_recently_used(router):
    r = router(["--models-max", "1"])
    load_model(r.port, MODEL_A)
    # touch MODEL_A so it becomes the most recently used one
    status, _ = http_request(r.port, "POST", "/v1/completions",
                             {"model": MODEL_A, "prompt": "hi",
                              "n_predict": 2}, timeout=30)
    assert status == 200, status

    # loading a second model beyond the limit evicts the other one
    load_model(r.port, MODEL_C)
    wait_model_status(r.port, MODEL_A, "unloaded", timeout=60)
    assert model_status(r.port, MODEL_C).get("value") == "loaded"

    # the evicted model can still serve again (it wins the next round)
    status, body = http_request(r.port, "POST", "/v1/completions",
                                {"model": MODEL_A, "prompt": "hi",
                                 "n_predict": 2},
                                timeout=MODEL_LOAD_TIMEOUT + 30)
    assert status == 200, (status, body)
    wait_model_status(r.port, MODEL_A, "loaded", timeout=60)
