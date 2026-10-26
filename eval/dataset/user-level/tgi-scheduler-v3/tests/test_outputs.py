# Behavior tests for the TGI router dual-protocol scheduler work.
#
# These tests build the router workspace from /app, stand up mock gRPC
# model shards (implemented in Python) that speak the generate.v2 and/or
# generate.v3 protocol over a unix socket, boot the real
# text-generation-router binary against them, and assert the externally
# visible contract:
#   * the new generate.v3 proto file exists at the agreed location,
#   * the client crate exposes versioned public modules plus the shared
#     ShardInfo struct and Health trait,
#   * the router auto-negotiates the protocol with the master shard,
#     logging "Using scheduler V3" / "Using scheduler V2",
#   * end-to-end HTTP behavior (/generate, /generate_stream, /health,
#     /info) works against a v3-only shard and stays working against a
#     legacy v2-only shard,
#   * the max_batch_total_tokens warmup semantics are preserved,
#   * the workspace builds and its own unit tests keep passing.

import importlib
import itertools
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

APP_DIR = "/app"
CLIENT_CRATE = "/app/router/client"
WORK_DIR = "/tmp/tgi-sched-verify"
ROUTER_BIN = "/app/target/debug/text-generation-router"

V2_PROTO = "/app/proto/generate.proto"
V3_PROTO = "/app/proto/v3/generate.proto"

TOKENIZER_DIR = os.path.join(WORK_DIR, "tokenizer")

MOCK_TOKEN = "mock"
MOCK_TEXT = "mock generated text"
MOCK_DTYPE = "float16"
MOCK_DEVICE = "cuda"

BUILD_TIMEOUT = 1500
UNIT_TEST_TIMEOUT = 1500
PROBE_BUILD_TIMEOUT = 900
BOOT_TIMEOUT = 120
EXIT_TIMEOUT = 90
HTTP_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd, cwd, log_path, timeout, env=None):
    with open(log_path, "w") as logf:
        proc = subprocess.run(cmd, cwd=cwd, stdout=logf,
                              stderr=subprocess.STDOUT, timeout=timeout,
                              env=env)
    return proc.returncode


def _log_tail(log_path, lines=40):
    try:
        with open(log_path, errors="replace") as f:
            return "".join(f.readlines()[-lines:])
    except OSError:
        return "<no log>"


def _http(method, port, path, body=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request("http://127.0.0.1:{0}{1}".format(port, path),
                                 data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
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


def _write_tokenizer():
    """Minimal WordLevel tokenizer so validation exercises the real
    tokenization path without any network access."""
    os.makedirs(TOKENIZER_DIR, exist_ok=True)
    tokenizer_json = {
        "version": "1.0",
        "truncation": None,
        "padding": None,
        "added_tokens": [],
        "normalizer": None,
        "pre_tokenizer": {"type": "Whitespace"},
        "post_processor": None,
        "decoder": None,
        "model": {
            "type": "WordLevel",
            "vocab": {"<unk>": 0, "Hello": 1, "world": 2, "liveness": 3},
            "unk_token": "<unk>",
        },
    }
    with open(os.path.join(TOKENIZER_DIR, "tokenizer.json"), "w") as f:
        json.dump(tokenizer_json, f)


def _compile_stub(version, src_proto, out_dir):
    """Generate Python gRPC stubs for one protocol version.

    The source .proto is copied to a versioned filename first so the two
    versions' python modules do not collide (both are named generate.proto).
    """
    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, "generate_{0}.proto".format(version))
    shutil.copy(src_proto, dst)
    from grpc_tools import protoc
    rc = protoc.main([
        "grpc_tools.protoc",
        "-I" + out_dir,
        "--python_out=" + out_dir,
        "--grpc_python_out=" + out_dir,
        dst,
    ])
    assert rc == 0, "protoc failed for {0}".format(src_proto)
    if out_dir not in sys.path:
        sys.path.insert(0, out_dir)
    pb2 = importlib.import_module("generate_{0}_pb2".format(version))
    pb2_grpc = importlib.import_module("generate_{0}_pb2_grpc".format(version))
    return pb2, pb2_grpc


def _make_servicer(pb2, pb2_grpc, sock_path, max_supported_total_tokens):
    """A mock shard with realistic batching semantics.

    A request finishes once it has produced ``max_new_tokens`` tokens across
    Prefill/Decode rounds; a batch that is not completely finished is handed
    back as a CachedBatch carrying *all* of its requests (finished members
    included), exactly like the real model server -- it is the router that
    filters finished members out via FilterBatch.  RPC call counters let the
    tests tell the two health-check paths (model-health Prefill liveness
    probe vs lightweight device Health RPC) apart.  Decode rounds are paced
    by a short delay so a mid-flight join is deterministic in tests.
    """

    decode_delay = 0.4

    class Servicer(pb2_grpc.TextGenerationServiceServicer):
        def __init__(self):
            self.health_calls = 0
            self.prefill_calls = 0
            self.decode_calls = 0
            self.concat_decode_calls = 0
            self.filter_calls = 0
            self._requests = {}      # request id -> {"generated", "max_new"}
            self._batch_ids = itertools.count(1)
            self._batch_max_tokens = {}

        def _token(self):
            return pb2.Tokens(ids=[1], logprobs=[-0.25],
                              texts=[MOCK_TOKEN], is_special=[False])

        def _generation(self, request_id, done, total):
            generation = pb2.Generation(request_id=request_id,
                                        tokens=self._token())
            if done:
                generation.generated_text.CopyFrom(pb2.GeneratedText(
                    text=MOCK_TEXT, generated_tokens=total,
                    finish_reason=pb2.FINISH_REASON_LENGTH))
            return generation

        def _cached_batch(self, request_ids, max_tokens):
            batch_id = next(self._batch_ids)
            self._batch_max_tokens[batch_id] = max_tokens
            return pb2.CachedBatch(
                id=batch_id, request_ids=request_ids,
                size=len(request_ids), max_tokens=max_tokens)

        def Info(self, request, context):
            return pb2.InfoResponse(
                requires_padding=False,
                dtype=MOCK_DTYPE,
                device_type=MOCK_DEVICE,
                speculate=0,
            )

        def ServiceDiscovery(self, request, context):
            # Mirrors the real (non-sharded) model server: the master shard
            # reports its own address as the only shard.
            return pb2.ServiceDiscoveryResponse(
                urls=["unix://" + sock_path])

        def ClearCache(self, request, context):
            return pb2.ClearCacheResponse()

        def FilterBatch(self, request, context):
            self.filter_calls += 1
            keep = [rid for rid in request.request_ids
                    if rid in self._requests]
            response = pb2.FilterBatchResponse()
            if keep:
                response.batch.CopyFrom(self._cached_batch(
                    keep, self._batch_max_tokens.get(request.batch_id, 0)))
            return response

        def Warmup(self, request, context):
            if max_supported_total_tokens is None:
                return pb2.WarmupResponse()
            return pb2.WarmupResponse(
                max_supported_total_tokens=max_supported_total_tokens)

        def Health(self, request, context):
            self.health_calls += 1
            return pb2.HealthResponse()

        def Prefill(self, request, context):
            self.prefill_calls += 1
            generations = []
            for req in request.batch.requests:
                max_new = req.stopping_parameters.max_new_tokens or 1
                self._requests[req.id] = {"generated": 1, "max_new": max_new}
                generations.append(self._generation(
                    req.id, done=max_new <= 1, total=1))
            kwargs = {}
            if any(not g.HasField("generated_text") for g in generations):
                kwargs["batch"] = self._cached_batch(
                    [req.id for req in request.batch.requests],
                    request.batch.max_tokens)
            return pb2.PrefillResponse(generations=generations,
                                       forward_ns=1000, decode_ns=1000,
                                       total_ns=2000, **kwargs)

        def Decode(self, request, context):
            self.decode_calls += 1
            if len(request.batches) > 1:
                self.concat_decode_calls += 1
            time.sleep(decode_delay)
            generations = []
            carried = []
            carried_max_tokens = 0
            for cached in request.batches:
                for rid in cached.request_ids:
                    state = self._requests.get(rid)
                    if state is None:
                        continue
                    state["generated"] += 1
                    generations.append(self._generation(
                        rid, done=state["generated"] >= state["max_new"],
                        total=state["generated"]))
                    carried.append(rid)
                    carried_max_tokens += cached.max_tokens
            kwargs = {}
            if any(not g.HasField("generated_text") for g in generations):
                kwargs["batch"] = self._cached_batch(
                    carried, carried_max_tokens)
            return pb2.DecodeResponse(generations=generations,
                                      forward_ns=1000, decode_ns=1000,
                                      total_ns=2000, **kwargs)

    return Servicer()


class MockShard(object):
    """gRPC server exposing the TextGenerationService for the requested
    protocol versions on a unix socket."""

    def __init__(self, versions, sock_path, stub_dir,
                 max_supported_total_tokens=None):
        grpc = pytest.importorskip("grpc")
        if os.path.exists(sock_path):
            os.unlink(sock_path)
        os.makedirs(os.path.dirname(sock_path), exist_ok=True)
        self.sock_path = sock_path
        from concurrent import futures
        self._server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
        self.servicers = {}
        for version in versions:
            src = V2_PROTO if version == "v2" else V3_PROTO
            pb2, pb2_grpc = _compile_stub(version, src, stub_dir)
            servicer = _make_servicer(pb2, pb2_grpc, sock_path,
                                      max_supported_total_tokens)
            add = pb2_grpc.add_TextGenerationServiceServicer_to_server
            add(servicer, self._server)
            self.servicers[version] = servicer
        bound = self._server.add_insecure_port("unix://" + sock_path)
        assert bound, "failed to bind unix socket {0}".format(sock_path)

    def start(self):
        self._server.start()

    def stop(self):
        self._server.stop(0)


def boot_router(sock_path, port, log_path, max_total_tokens=256):
    args = [
        ROUTER_BIN,
        "--master-shard-uds-path", sock_path,
        "--tokenizer-name", TOKENIZER_DIR,
        "--hostname", "127.0.0.1",
        "--port", str(port),
        "--max-input-tokens", "128",
        "--max-total-tokens", str(max_total_tokens),
        "--max-batch-prefill-tokens", "512",
        "--max-concurrent-requests", "8",
        "--max-best-of", "2",
        "--validation-workers", "1",
    ]
    logf = open(log_path, "w")
    proc = subprocess.Popen(args, stdout=logf, stderr=subprocess.STDOUT)
    return proc, logf


def wait_ready(proc, port, log_path):
    deadline = time.monotonic() + BOOT_TIMEOUT
    while True:
        if proc.poll() is not None:
            raise AssertionError(
                "router exited with code {0} during startup; log tail:\n{1}"
                .format(proc.returncode, _log_tail(log_path)))
        try:
            status, _ = _http("GET", port, "/health")
            if status == 200:
                return
        except Exception:
            pass
        if time.monotonic() > deadline:
            raise AssertionError(
                "router did not become healthy in time; log tail:\n{0}"
                .format(_log_tail(log_path)))
        time.sleep(1)


def stop_router(proc, logf):
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=15)
    logf.close()


def read_log(log_path):
    with open(log_path, errors="replace") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Session fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def cargo_build():
    """The whole workspace must build; caches are pre-warmed in the image
    so this is an incremental rebuild of whatever was changed."""
    os.makedirs(WORK_DIR, exist_ok=True)
    log_path = os.path.join(WORK_DIR, "cargo-build.log")
    rc = _run(["cargo", "build", "--workspace"], APP_DIR, log_path,
              BUILD_TIMEOUT)
    assert rc == 0, "cargo build --workspace failed; tail:\n{0}".format(
        _log_tail(log_path))
    assert os.path.isfile(ROUTER_BIN), \
        "router binary missing after build: {0}".format(ROUTER_BIN)
    _write_tokenizer()
    return True


@pytest.fixture(scope="module")
def v3_stack(cargo_build):
    """Router booted against a shard that speaks *only* generate.v3."""
    if not os.path.isfile(V3_PROTO):
        pytest.fail("expected v3 proto at {0}".format(V3_PROTO))
    port = 3081
    sock = os.path.join(WORK_DIR, "v3", "shard.sock")
    log_path = os.path.join(WORK_DIR, "router-v3.log")
    shard = MockShard(["v3"], sock, os.path.join(WORK_DIR, "stubs-v3"),
                      max_supported_total_tokens=4096)
    shard.start()
    proc, logf = boot_router(sock, port, log_path)
    try:
        wait_ready(proc, port, log_path)
        servicer = shard.servicers["v3"]
        yield {"port": port, "log": log_path, "shard": shard,
               # wait_ready() polled /health before any generation existed,
               # so these counters capture the pre-generation health path.
               "boot_health_calls": servicer.health_calls,
               "boot_prefill_calls": servicer.prefill_calls}
    finally:
        stop_router(proc, logf)
        shard.stop()


@pytest.fixture(scope="module")
def v2_stack(cargo_build):
    """Router booted against a legacy shard that speaks *only* generate.v2
    and does not report a max-batch-total-tokens cap at warmup."""
    port = 3082
    sock = os.path.join(WORK_DIR, "v2", "shard.sock")
    log_path = os.path.join(WORK_DIR, "router-v2.log")
    shard = MockShard(["v2"], sock, os.path.join(WORK_DIR, "stubs-v2"),
                      max_supported_total_tokens=None)
    shard.start()
    proc, logf = boot_router(sock, port, log_path)
    try:
        wait_ready(proc, port, log_path)
        yield {"port": port, "log": log_path}
    finally:
        stop_router(proc, logf)
        shard.stop()


# ---------------------------------------------------------------------------
# Static contract: the new protocol file
# ---------------------------------------------------------------------------

def test_v3_proto_file_contract():
    assert os.path.isfile(V3_PROTO), \
        "v3 protocol file missing: {0}".format(V3_PROTO)
    with open(V3_PROTO, errors="replace") as f:
        content = f.read()
    assert "package generate.v3;" in content, \
        "v3 proto must declare package generate.v3"
    assert "service TextGenerationService" in content
    for rpc in ["Info", "ServiceDiscovery", "ClearCache", "FilterBatch",
                "Warmup", "Prefill", "Decode", "Health"]:
        assert "rpc {0}".format(rpc) in content, \
            "v3 proto must keep the {0} rpc".format(rpc)


# ---------------------------------------------------------------------------
# Static contract: the client crate's versioned public API
# ---------------------------------------------------------------------------

def test_client_crate_versioned_public_api(cargo_build):
    probe_dir = os.path.join(WORK_DIR, "client-api-probe")
    os.makedirs(os.path.join(probe_dir, "src"), exist_ok=True)
    # Pin the probe to the workspace's own lockfile so the build resolves
    # entirely from the pre-populated cargo cache (offline).
    shutil.copy(os.path.join(APP_DIR, "Cargo.lock"),
                os.path.join(probe_dir, "Cargo.lock"))
    with open(os.path.join(probe_dir, "Cargo.toml"), "w") as f:
        f.write(
            "[package]\n"
            "name = \"client-api-probe\"\n"
            "version = \"0.1.0\"\n"
            "edition = \"2021\"\n\n"
            "[dependencies]\n"
            "text-generation-client = { path = \"" + CLIENT_CRATE + "\" }\n"
        )
    with open(os.path.join(probe_dir, "src", "main.rs"), "w") as f:
        f.write(
            "use text_generation_client::{v2, v3, Health, ShardInfo};\n"
            "\n"
            "fn assert_health<T: Health>() {}\n"
            "\n"
            "fn main() {\n"
            "    assert_health::<v2::ShardedClient>();\n"
            "    assert_health::<v3::ShardedClient>();\n"
            "    let _client_v2: Option<v2::Client> = None;\n"
            "    let _client_v3: Option<v3::Client> = None;\n"
            "    let _ = ShardInfo {\n"
            "        requires_padding: false,\n"
            "        dtype: String::new(),\n"
            "        device_type: String::new(),\n"
            "        window_size: None,\n"
            "        speculate: 0,\n"
            "    };\n"
            "    let _ = v2::Batch::default();\n"
            "    let _ = v3::Batch::default();\n"
            "    let _ = v2::Request::default();\n"
            "    let _ = v3::Request::default();\n"
            "    let _ = v2::NextTokenChooserParameters::default();\n"
            "    let _ = v3::NextTokenChooserParameters::default();\n"
            "    let _ = v2::StoppingCriteriaParameters::default();\n"
            "    let _ = v3::StoppingCriteriaParameters::default();\n"
            "    let _ = v2::Generation::default();\n"
            "    let _ = v3::Generation::default();\n"
            "    println!(\"client crate versioned API OK\");\n"
            "}\n"
        )
    log_path = os.path.join(WORK_DIR, "probe-build.log")
    env = dict(os.environ)
    # Reuse the workspace's already-compiled dependency artifacts.
    env["CARGO_TARGET_DIR"] = "/app/target"
    rc = _run(["cargo", "build", "--offline"], probe_dir, log_path,
              PROBE_BUILD_TIMEOUT, env=env)
    assert rc == 0, \
        "client crate does not expose the agreed versioned public API; " \
        "tail:\n{0}".format(_log_tail(log_path))


# ---------------------------------------------------------------------------
# The workspace's own unit tests keep passing
# ---------------------------------------------------------------------------

def test_router_and_client_unit_tests(cargo_build):
    log_path = os.path.join(WORK_DIR, "cargo-test.log")
    rc = _run(["cargo", "test", "-p", "text-generation-router",
               "-p", "text-generation-client"],
              APP_DIR, log_path, UNIT_TEST_TIMEOUT)
    assert rc == 0, "cargo test failed; tail:\n{0}".format(
        _log_tail(log_path))


# ---------------------------------------------------------------------------
# Scenario 1: v3-only shard -> SchedulerV3
# ---------------------------------------------------------------------------

def test_v3_scheduler_selected_and_logged(v3_stack):
    log = read_log(v3_stack["log"])
    assert "Using scheduler V3" in log, \
        "router must log the selected scheduler version; log tail:\n{0}" \
        .format(_log_tail(v3_stack["log"]))


def test_v3_generate_end_to_end(v3_stack):
    status, body = _http("POST", v3_stack["port"], "/generate", {
        "inputs": "Hello world",
        "parameters": {"max_new_tokens": 1, "details": True},
    })
    assert status == 200, (status, body)
    assert body.get("generated_text") == MOCK_TEXT, body
    details = body.get("details") or {}
    assert details.get("finish_reason") == "length", body
    assert details.get("generated_tokens") == 1, body
    tokens = details.get("tokens") or []
    assert tokens and tokens[0].get("text") == MOCK_TOKEN, body


def test_v3_generate_stream_end_to_end(v3_stack):
    req = urllib.request.Request(
        "http://127.0.0.1:{0}/generate_stream".format(v3_stack["port"]),
        data=json.dumps({
            "inputs": "Hello world",
            "parameters": {"max_new_tokens": 1},
        }).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    events = []
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        assert resp.status == 200
        deadline = time.monotonic() + HTTP_TIMEOUT
        while time.monotonic() < deadline and len(events) < 20:
            line = resp.readline()
            if not line:
                break
            line = line.decode(errors="replace").strip()
            if not line.startswith("data:"):
                continue
            event = json.loads(line[len("data:"):].strip())
            events.append(event)
            if event.get("generated_text"):
                break
    assert events, "no SSE events received from /generate_stream"
    assert events[0].get("token", {}).get("text") == MOCK_TOKEN, events
    assert events[-1].get("generated_text") == MOCK_TEXT, events


def test_v3_health_and_info(v3_stack):
    servicer = v3_stack["shard"].servicers["v3"]
    # The readiness probe ran before any generation existed: /health must
    # have used the expensive model-health path (a real Prefill liveness
    # probe), not the lightweight device Health RPC.
    assert v3_stack["boot_prefill_calls"] >= 1, \
        "no liveness Prefill observed at boot: /health must use " \
        "model_health until a generation has succeeded"
    assert v3_stack["boot_health_calls"] == 0, \
        "the device Health RPC was used before any successful generation: " \
        "the model_health/device_health states are inverted"

    # Generation has succeeded (the /generate tests above): /health must now
    # use the lightweight device Health RPC and must not issue further
    # Prefill liveness probes.
    health_before = servicer.health_calls
    prefill_before = servicer.prefill_calls
    status, _ = _http("GET", v3_stack["port"], "/health")
    assert status == 200, status
    assert servicer.health_calls == health_before + 1, \
        "after a successful generation /health must switch to the " \
        "lightweight device_health RPC"
    assert servicer.prefill_calls == prefill_before, \
        "/health must not issue new liveness Prefills once generation is " \
        "healthy"

    status, body = _http("GET", v3_stack["port"], "/info")
    assert status == 200, (status, body)
    # Shard info gathered over the v3 protocol must reach the HTTP API.
    assert body.get("model_dtype") == MOCK_DTYPE, body
    assert body.get("model_device_type") == MOCK_DEVICE, body
    # The shard reported a 4096-token cap at warmup; it wins.
    assert body.get("max_batch_total_tokens") == 4096, body


def test_v3_decode_filter_batch_and_concat_paths(v3_stack):
    """Multi-token generations must drive Prefill->Decode rounds.

    A short request joining while a long one is mid-generation exercises
    batch concatenation (one Decode call spanning two cached batches) and
    FilterBatch (the finished short request must be filtered out of the
    cached batch the shard handed back).
    """
    servicer = v3_stack["shard"].servicers["v3"]
    port = v3_stack["port"]
    results = {}

    def generate(tag, inputs, max_new):
        results[tag] = _http("POST", port, "/generate", {
            "inputs": inputs,
            "parameters": {"max_new_tokens": max_new, "details": True},
        })

    prefill_before = servicer.prefill_calls
    decode_before = servicer.decode_calls
    long_req = threading.Thread(
        target=generate, args=("long", "Hello world", 6))
    long_req.start()
    # Wait until the long request has actually been prefilled, then let the
    # short one join mid-flight (the stub paces decode rounds, leaving a
    # multi-second join window).
    deadline = time.monotonic() + HTTP_TIMEOUT
    while servicer.prefill_calls == prefill_before:
        if time.monotonic() > deadline:
            long_req.join(timeout=1)
            raise AssertionError("long request never reached a Prefill")
        time.sleep(0.05)
    generate("short", "Hello liveness", 2)
    long_req.join(timeout=HTTP_TIMEOUT)

    for tag, expected_tokens in (("long", 6), ("short", 2)):
        status, body = results[tag]
        assert status == 200, (tag, status, body)
        details = (body or {}).get("details") or {}
        assert details.get("generated_tokens") == expected_tokens, (tag, body)
        assert details.get("finish_reason") == "length", (tag, body)
        tokens = details.get("tokens") or []
        assert len(tokens) == expected_tokens, (tag, body)

    assert servicer.decode_calls > decode_before, \
        "multi-token generation never drove the Decode RPC"
    assert servicer.concat_decode_calls >= 1, \
        "mid-flight join never produced a multi-batch (concatenating) Decode"
    assert servicer.filter_calls >= 1, \
        "a partially-finished batch never drove the FilterBatch RPC"


# ---------------------------------------------------------------------------
# Scenario 2: legacy v2-only shard -> SchedulerV2 fallback
# ---------------------------------------------------------------------------

def test_v2_fallback_selected_and_logged(v2_stack):
    log = read_log(v2_stack["log"])
    assert "Using scheduler V2" in log, \
        "router must fall back to v2 and log it; log tail:\n{0}" \
        .format(_log_tail(v2_stack["log"]))


def test_v2_generate_backward_compatible(v2_stack):
    status, body = _http("POST", v2_stack["port"], "/generate", {
        "inputs": "Hello world",
        "parameters": {"max_new_tokens": 1, "details": True},
    })
    assert status == 200, (status, body)
    assert body.get("generated_text") == MOCK_TEXT, body
    details = body.get("details") or {}
    assert details.get("finish_reason") == "length", body
    assert details.get("generated_tokens") == 1, body

    # This shard did not report a max-batch-total-tokens cap at warmup, so
    # the router falls back to max(16000, max_total_tokens,
    # max_batch_prefill_tokens).
    status, body = _http("GET", v2_stack["port"], "/info")
    assert status == 200, (status, body)
    assert body.get("max_batch_total_tokens") == 16000, body


# ---------------------------------------------------------------------------
# Warmup semantics: exceeding the shard-reported cap is fatal
# ---------------------------------------------------------------------------

def test_max_total_tokens_over_shard_cap_exits(cargo_build):
    port = 3083
    sock = os.path.join(WORK_DIR, "cap", "shard.sock")
    log_path = os.path.join(WORK_DIR, "router-cap.log")
    shard = MockShard(["v2"], sock, os.path.join(WORK_DIR, "stubs-cap"),
                      max_supported_total_tokens=4096)
    shard.start()
    try:
        proc, logf = boot_router(sock, port, log_path, max_total_tokens=5000)
        try:
            deadline = time.monotonic() + EXIT_TIMEOUT
            while proc.poll() is None and time.monotonic() < deadline:
                time.sleep(1)
            assert proc.poll() is not None, \
                "router kept running with max-total-tokens above the " \
                "shard-reported cap; log tail:\n{0}".format(
                    _log_tail(log_path))
            assert proc.returncode != 0, proc.returncode
        finally:
            stop_router(proc, logf)
    finally:
        shard.stop()
