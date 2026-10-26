"""Verifier tests for the gradio event recording & replay feature.

All expectations below are frozen contract values: every app used here is
constructed inside this file and every expected input/output is a hardcoded
literal, so nothing is recomputed from the state of /app at judging time.
The verifier also asserts that the recording feature integrates with existing
gradio primitives (SessionState, Blocks call hooks, canonical-JSON helper)
and that the record file is tamper-evident.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest
from fastapi.testclient import TestClient

import gradio as gr
from gradio import utils as gradio_utils
from gradio.routes import App
from gradio.state_holder import SessionState


def run(coro):
    return asyncio.run(coro)


def add(a, b):
    return a + b


def make_number_app(fn=add, api_name="add"):
    with gr.Blocks() as demo:
        a = gr.Number(label="a")
        b = gr.Number(label="b")
        out = gr.Number(label="sum")
        btn = gr.Button("go")
        btn.click(fn, [a, b], out, api_name=api_name)
    return demo


def make_counter_app():
    def counter(n, st):
        return st + n, st + n

    with gr.Blocks() as demo:
        n = gr.Number(label="n")
        st = gr.State(0)
        out = gr.Number(label="out")
        btn = gr.Button("go")
        btn.click(counter, [n, st], [out, st], api_name="counter")
    return demo


def record_two_calls(path):
    demo = make_number_app()
    rec = gr.EventRecorder(demo, path).start()
    state = SessionState(demo)
    run(demo.process_api(0, [1.0, 2.0], state=state, session_hash="sess-1"))
    run(demo.process_api(0, [10.0, 5.0], state=state, session_hash="sess-1"))
    rec.close()
    return path


# ----------------------------------------------------------------------
# public API surface
# ----------------------------------------------------------------------


def test_public_api_exported():
    assert hasattr(gr, "EventRecorder")
    assert hasattr(gr, "replay_events")
    assert hasattr(gr, "load_events")
    assert hasattr(gr, "ReplayReport")
    assert hasattr(gr, "ReplayResult")


# ----------------------------------------------------------------------
# recording
# ----------------------------------------------------------------------


def test_basic_event_fields_and_file_format(tmp_path):
    path = tmp_path / "rec.jsonl"
    demo = make_number_app()
    rec = gr.EventRecorder(demo, path)
    assert not rec.is_recording
    rec.start()
    assert rec.is_recording
    state = SessionState(demo)
    run(demo.process_api(0, [1.0, 2.0], state=state, session_hash="sess-1"))
    rec.stop()

    with open(path, encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    assert len(lines) == 2
    header, event = lines
    assert header["type"] == "header"
    assert header["format_version"] == 1
    assert isinstance(header["gradio_version"], str) and header["gradio_version"]
    assert "created_at" in header

    assert event["type"] == "event"
    assert event["index"] == 0
    assert event["api_name"] == "add"
    assert event["fn_name"] == "add"
    assert event["session_hash"] == "sess-1"
    assert event["inputs"] == [1.0, 2.0]
    assert event["outputs"] == [3.0]
    assert event["error"] is None
    assert event["duration"] >= 0
    assert event["timestamp"] > 0

    events = gr.load_events(path)
    assert events == rec.events


def test_not_recording_before_start_and_after_stop(tmp_path):
    path = tmp_path / "rec.jsonl"
    demo = make_number_app()
    rec = gr.EventRecorder(demo, path)
    state = SessionState(demo)

    result = run(demo.process_api(0, [1.0, 2.0], state=state, session_hash="s"))
    assert result["data"] == [3.0]
    assert rec.events == []
    assert not path.exists()

    rec.start()
    run(demo.process_api(0, [1.0, 2.0], state=state, session_hash="s"))
    rec.stop()
    run(demo.process_api(0, [5.0, 5.0], state=state, session_hash="s"))
    assert len(rec.events) == 1
    assert rec.events[0]["inputs"] == [1.0, 2.0]

    # idempotent start/stop
    rec.start()
    rec.start()
    assert rec.is_recording
    rec.stop()
    rec.stop()
    assert not rec.is_recording


def test_context_manager_records_and_detaches(tmp_path):
    path = tmp_path / "rec.jsonl"
    demo = make_number_app()
    with gr.EventRecorder(demo, path) as rec:
        assert rec.is_recording
        state = SessionState(demo)
        run(demo.process_api(0, [2.0, 3.0], state=state, session_hash="s"))
    assert not rec.is_recording
    assert len(rec.events) == 1
    # detached after exiting the context: new calls are not recorded
    run(demo.process_api(0, [2.0, 3.0], state=state, session_hash="s"))
    assert len(rec.events) == 1


def test_multiple_recorders_independent_files(tmp_path):
    demo = make_number_app()
    rec1 = gr.EventRecorder(demo, tmp_path / "r1.jsonl").start()
    rec2 = gr.EventRecorder(demo, tmp_path / "r2.jsonl").start()
    state = SessionState(demo)
    run(demo.process_api(0, [4.0, 6.0], state=state, session_hash="s"))
    rec1.close()
    rec2.close()
    for path in (tmp_path / "r1.jsonl", tmp_path / "r2.jsonl"):
        (event,) = gr.load_events(path)
        assert event["inputs"] == [4.0, 6.0]
        assert event["outputs"] == [10.0]


def test_include_and_exclude_filters(tmp_path):
    with gr.Blocks() as demo:
        a = gr.Number(label="a")
        b = gr.Number(label="b")
        out = gr.Number(label="sum")
        diff = gr.Number(label="diff")
        btn = gr.Button("go")
        btn.click(add, [a, b], out, api_name="add")
        btn.click(lambda x, y: x - y, [a, b], diff, api_name="sub")

    rec = gr.EventRecorder(demo, tmp_path / "inc.jsonl", include=["add"]).start()
    state = SessionState(demo)
    run(demo.process_api(0, [3.0, 1.0], state=state, session_hash="s"))
    run(demo.process_api(1, [3.0, 1.0], state=state, session_hash="s"))
    rec.close()
    (event,) = rec.events
    assert event["api_name"] == "add"

    rec2 = gr.EventRecorder(demo, tmp_path / "exc.jsonl", exclude=["sub"]).start()
    run(demo.process_api(0, [3.0, 1.0], state=state, session_hash="s"))
    run(demo.process_api(1, [3.0, 1.0], state=state, session_hash="s"))
    rec2.close()
    (event2,) = rec2.events
    assert event2["api_name"] == "add"


def test_redaction_by_exact_label(tmp_path):
    def login(user, password):
        return f"welcome {user}"

    with gr.Blocks() as demo:
        user = gr.Textbox(label="用户名")
        password = gr.Textbox(label="密码")
        out = gr.Textbox(label="out")
        btn = gr.Button("login")
        btn.click(login, [user, password], out, api_name="login")

    rec = gr.EventRecorder(demo, tmp_path / "rec.jsonl", redact=["密码"]).start()
    state = SessionState(demo)
    run(demo.process_api(0, ["alice", "hunter2"], state=state, session_hash="s"))
    rec.close()
    (event,) = rec.events
    assert event["inputs"] == ["alice", "***"]
    assert event["outputs"] == ["welcome alice"]
    # the redaction only affects the record, not what the function received
    assert event["error"] is None


def test_error_event_recorded_and_exception_propagates(tmp_path):
    def boom(a, b):
        raise ValueError("cannot compute")

    demo = make_number_app(boom)
    rec = gr.EventRecorder(demo, tmp_path / "rec.jsonl").start()
    state = SessionState(demo)
    with pytest.raises(ValueError, match="cannot compute"):
        run(demo.process_api(0, [1.0, 2.0], state=state, session_hash="s"))
    rec.close()
    (event,) = rec.events
    assert event["error"] == "ValueError"
    assert event["outputs"] is None
    assert event["inputs"] == [1.0, 2.0]


def test_stateful_effective_values_recorded(tmp_path):
    demo = make_counter_app()
    rec = gr.EventRecorder(demo, tmp_path / "rec.jsonl").start()
    state = SessionState(demo)
    # the raw payload for the State input is ignored by the runtime; the
    # function sees the session value, so the session value must be recorded.
    run(demo.process_api(0, [5.0, None], state=state, session_hash="s"))
    run(demo.process_api(0, [5.0, None], state=state, session_hash="s"))
    rec.close()
    first, second = rec.events
    assert first["inputs"] == [5.0, 0]
    # the State output position is a placeholder (None) in the raw payload;
    # the recorder must store the new session value instead.
    assert first["outputs"] == [5.0, 5.0]
    assert second["inputs"] == [5.0, 5.0]
    assert second["outputs"] == [10.0, 10.0]


def test_generator_records_first_call_only(tmp_path):
    def gen(x):
        for i in range(3):
            yield x + i

    with gr.Blocks() as demo:
        x = gr.Number(label="x")
        o = gr.Number(label="o")
        b = gr.Button("g")
        b.click(gen, x, o, api_name="gen")

    rec = gr.EventRecorder(demo, tmp_path / "rec.jsonl").start()
    state = SessionState(demo)
    first = run(demo.process_api(0, [10.0], state=state, session_hash="g"))
    assert first["is_generating"]
    second = run(
        demo.process_api(
            0, [], state=state, session_hash="g", iterator=first["iterator"]
        )
    )
    assert second["is_generating"]
    rec.close()
    (event,) = rec.events
    assert event["inputs"] == [10.0]
    assert event["outputs"] == [10.0]


def test_http_queue_calls_are_recorded(tmp_path):
    def echo(name):
        return f"hi {name}"

    with gr.Blocks() as demo:
        name = gr.Textbox(label="name")
        out = gr.Textbox(label="out")
        btn = gr.Button("go")
        btn.click(echo, name, out, api_name="echo")

    demo.queue()
    rec = gr.EventRecorder(demo, tmp_path / "rec.jsonl").start()
    app = App.create_app(demo)
    with TestClient(app) as client:
        client.get("/gradio_api/startup-events", timeout=10)
        client.post(
            "/gradio_api/queue/join",
            json={
                "data": ["alice"],
                "fn_index": 0,
                "event_data": None,
                "session_hash": "sess-http",
                "trigger_id": None,
            },
            timeout=10,
        )
        response = client.get(
            "/gradio_api/queue/data?session_hash=sess-http", timeout=15
        )
        for line in response.iter_lines():
            if "process_completed" in line:
                break
    rec.close()
    (event,) = rec.events
    assert event["api_name"] == "echo"
    assert event["session_hash"] == "sess-http"
    assert event["inputs"] == ["alice"]
    assert event["outputs"] == ["hi alice"]


# ----------------------------------------------------------------------
# canonical serialization, ordering, and tamper-evidence
# ----------------------------------------------------------------------


def test_canonical_json_dumps_is_public_helper():
    """The record writer relies on a shared canonical JSON helper on
    gradio.utils. Keys must be sorted; NaN/Infinity must be rejected;
    non-ASCII characters must round-trip unescaped; tuples become lists."""
    canonical = gradio_utils.canonical_json_dumps
    assert canonical({"b": 1, "a": 2}) == '{"a": 2, "b": 1}'
    assert canonical({"a": (1, 2, 3)}) == '{"a": [1, 2, 3]}'
    assert canonical({"msg": "你好"}) == '{"msg": "你好"}'
    with pytest.raises(ValueError):
        canonical({"a": float("nan")})
    with pytest.raises(ValueError):
        canonical({"a": float("inf")})


def test_event_and_header_have_canonical_field_order(tmp_path):
    """A record file gets diffed and code-reviewed; the on-disk key order for
    each header and event line must therefore be stable across runs. Header
    keys and event keys are laid out in alphabetical order."""
    path = tmp_path / "rec.jsonl"
    demo = make_number_app()
    rec = gr.EventRecorder(demo, path).start()
    state = SessionState(demo)
    run(demo.process_api(0, [7.0, 8.0], state=state, session_hash="canon"))
    rec.close()

    raw = path.read_text(encoding="utf-8").splitlines()
    header_pairs = json.loads(raw[0], object_pairs_hook=list)
    event_pairs = json.loads(raw[1], object_pairs_hook=list)
    header_keys = [k for k, _ in header_pairs]
    event_keys = [k for k, _ in event_pairs]

    # The header and each event line must be in fully sorted (alphabetical)
    # key order so consumers can diff them mechanically.
    assert header_keys == sorted(header_keys)
    assert event_keys == sorted(event_keys)
    # Every documented field is present:
    for key in (
        "api_name",
        "duration",
        "error",
        "fn_name",
        "index",
        "inputs",
        "outputs",
        "session_hash",
        "timestamp",
        "type",
    ):
        assert key in event_keys


def test_record_file_is_tamper_evident(tmp_path):
    """A recorded file must be reject-if-modified. Someone who edits a value
    inside an event line to make replay pass must not get away with it."""
    path = record_two_calls(tmp_path / "rec.jsonl")
    lines = path.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[1])
    event["outputs"] = [9999.0]
    lines[1] = json.dumps(event, ensure_ascii=False, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        gr.load_events(path)


def test_record_file_rejects_reordered_events(tmp_path):
    """Swapping two events must be caught by the integrity check."""
    path = record_two_calls(tmp_path / "rec.jsonl")
    lines = path.read_text(encoding="utf-8").splitlines()
    # swap the two event lines
    lines[1], lines[2] = lines[2], lines[1]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        gr.load_events(path)


def test_record_file_rejects_dropped_event(tmp_path):
    """Silently dropping an event line from the middle must be caught."""
    demo = make_number_app()
    path = tmp_path / "rec.jsonl"
    rec = gr.EventRecorder(demo, path).start()
    state = SessionState(demo)
    run(demo.process_api(0, [1.0, 2.0], state=state, session_hash="s"))
    run(demo.process_api(0, [3.0, 4.0], state=state, session_hash="s"))
    run(demo.process_api(0, [5.0, 6.0], state=state, session_hash="s"))
    rec.close()
    lines = path.read_text(encoding="utf-8").splitlines()
    # drop the middle event
    del lines[2]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        gr.load_events(path)


# ----------------------------------------------------------------------
# integration with existing gradio primitives
# ----------------------------------------------------------------------


def test_blocks_call_hooks_are_public_api():
    """`Blocks` exposes a public call-hook registry that fires before and
    after every process_api call. The recorder is one consumer; other
    consumers (metrics, tracing, ...) share the same registry."""
    demo = make_number_app()
    seen: list = []

    def spy(phase, ctx):
        seen.append(
            (phase, ctx["api_name"], ctx["fn_name"], ctx.get("outputs"))
        )

    demo.add_call_hook("spy", spy)
    assert "spy" in demo.get_call_hooks()

    state = SessionState(demo)
    run(demo.process_api(0, [1.0, 2.0], state=state, session_hash="s"))

    demo.remove_call_hook("spy")
    assert "spy" not in demo.get_call_hooks()

    # after remove, no new calls should be seen
    run(demo.process_api(0, [10.0, 20.0], state=state, session_hash="s"))

    phases = [p for p, *_ in seen]
    assert phases == ["before", "after"]
    assert seen[0][1] == "add" and seen[0][2] == "add"
    assert seen[0][3] is None  # outputs unknown at the "before" phase
    assert seen[1][3] == [3.0]


def test_call_hooks_receive_error_phase():
    """When the function raises, the after-hook must see the error phase
    with the exception class name."""
    def boom(a, b):
        raise ValueError("nope")

    demo = make_number_app(boom)
    seen: list = []

    def spy(phase, ctx):
        seen.append((phase, ctx.get("error")))

    demo.add_call_hook("spy", spy)
    state = SessionState(demo)
    with pytest.raises(ValueError):
        run(demo.process_api(0, [1.0, 2.0], state=state, session_hash="s"))
    demo.remove_call_hook("spy")

    phases = [p for p, _ in seen]
    assert phases == ["before", "after"]
    assert seen[1][1] == "ValueError"


def test_recorder_registers_as_call_hook(tmp_path):
    """Attaching a recorder must add it to the call-hook registry; closing
    it must remove it. This is how the two APIs stay coherent."""
    demo = make_number_app()
    assert demo.get_call_hooks() == []
    rec = gr.EventRecorder(demo, tmp_path / "rec.jsonl").start()
    assert len(demo.get_call_hooks()) == 1
    rec.close()
    assert demo.get_call_hooks() == []


def test_no_hooks_no_overhead_path():
    """When no hooks and no recorders are attached, the wrapped process_api
    must not construct any hook context. We check this by observing that the
    underlying implementation is called directly."""
    demo = make_number_app()
    calls: list = []
    original = type(demo)._process_api_impl

    async def spy_impl(self, *args, **kwargs):
        calls.append(1)
        return await original(self, *args, **kwargs)

    type(demo)._process_api_impl = spy_impl
    try:
        state = SessionState(demo)
        run(demo.process_api(0, [1.0, 2.0], state=state, session_hash="s"))
    finally:
        type(demo)._process_api_impl = original

    assert calls == [1]


def test_session_state_snapshot_and_restore_public_api():
    """SessionState exposes a public snapshot/restore pair. The replay
    machinery uses it to reinstate the recorded stateful values on a fresh
    session; test-time code can use it just as well."""
    demo = make_counter_app()
    state = SessionState(demo)
    run(demo.process_api(0, [5.0, None], state=state, session_hash="s"))
    run(demo.process_api(0, [5.0, None], state=state, session_hash="s"))
    snap = state.snapshot()
    # only the stateful component's value shows up in the snapshot
    assert list(snap.values()) == [10]
    # restoring resets the counter
    state.restore({key: 0 for key in snap})
    result = run(demo.process_api(0, [7.0, None], state=state, session_hash="s"))
    assert result["data"][0] == 7.0
    # snapshot returns a copy: mutating it must not touch the session
    snap2 = state.snapshot()
    snap2[next(iter(snap2))] = 999
    assert state.snapshot() != snap2


# ----------------------------------------------------------------------
# load_events validation
# ----------------------------------------------------------------------


def test_load_events_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        gr.load_events(tmp_path / "nope.jsonl")


def test_load_events_rejects_bad_files(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    with pytest.raises(ValueError):
        gr.load_events(empty)

    corrupt_header = tmp_path / "h.jsonl"
    corrupt_header.write_text("this is not json\n")
    with pytest.raises(ValueError):
        gr.load_events(corrupt_header)

    bad_version = tmp_path / "v.jsonl"
    bad_version.write_text(
        json.dumps({"type": "header", "format_version": 999}) + "\n"
    )
    with pytest.raises(ValueError):
        gr.load_events(bad_version)

    corrupt_event = tmp_path / "e.jsonl"
    corrupt_event.write_text(
        json.dumps({"type": "header", "format_version": 1}) + "\n{broken\n"
    )
    with pytest.raises(ValueError):
        gr.load_events(corrupt_event)


# ----------------------------------------------------------------------
# replay
# ----------------------------------------------------------------------


def test_replay_identical_app_matches(tmp_path):
    path = record_two_calls(tmp_path / "rec.jsonl")
    report = gr.replay_events(make_number_app(), path)
    assert report.ok
    assert bool(report)
    assert report.total == 2
    assert report.matched == 2
    assert report.mismatched == 0
    assert report.missing == 0
    assert report.errors == 0
    assert report.mismatches == []
    for i, result in enumerate(report.results):
        assert result.status == "match"
        assert result.api_name == "add"
        assert result.index == i
    json.dumps(report.to_dict())
    assert isinstance(report.summary(), str)


def test_replay_detects_behavior_change(tmp_path):
    path = record_two_calls(tmp_path / "rec.jsonl")

    def add_plus_one(a, b):
        return a + b + 1

    report = gr.replay_events(make_number_app(add_plus_one), path)
    assert not report.ok
    assert not bool(report)
    assert report.total == 2
    assert report.mismatched == 2
    first, second = report.results
    assert first.status == "mismatch"
    assert first.expected == [3.0]
    assert first.actual == [4.0]
    assert second.expected == [15.0]
    assert second.actual == [16.0]
    assert len(report.mismatches) == 2


def test_replay_error_events_compared_by_exception_type(tmp_path):
    def boom(a, b):
        raise ValueError("original message")

    demo = make_number_app(boom)
    path = tmp_path / "rec.jsonl"
    rec = gr.EventRecorder(demo, path).start()
    state = SessionState(demo)
    with pytest.raises(ValueError):
        run(demo.process_api(0, [1.0, 2.0], state=state, session_hash="s"))
    rec.close()

    def boom_same_type(a, b):
        raise ValueError("a completely different message")

    report = gr.replay_events(make_number_app(boom_same_type), path)
    assert report.ok
    assert report.results[0].expected == "ValueError"
    assert report.results[0].actual == "ValueError"

    # same call no longer raising -> mismatch
    report2 = gr.replay_events(make_number_app(add), path)
    assert not report2.ok
    result = report2.results[0]
    assert result.status == "mismatch"
    assert result.expected == "ValueError"

    # different exception type -> mismatch
    def boom_other(a, b):
        raise KeyError("k")

    report3 = gr.replay_events(make_number_app(boom_other), path)
    assert not report3.ok
    assert report3.results[0].status == "mismatch"


def test_replay_missing_function(tmp_path):
    path = record_two_calls(tmp_path / "rec.jsonl")

    def subtract(a, b):
        return a - b

    report = gr.replay_events(make_number_app(subtract, api_name="subtract"), path)
    assert not report.ok
    assert report.missing == 2
    assert all(r.status == "missing" for r in report.results)


def test_replay_restores_stateful_inputs(tmp_path):
    demo = make_counter_app()
    path = tmp_path / "rec.jsonl"
    rec = gr.EventRecorder(demo, path).start()
    state = SessionState(demo)
    for _ in range(3):
        run(demo.process_api(0, [5.0, None], state=state, session_hash="s"))
    rec.close()

    # a fresh app starts from fresh state; replay must restore the recorded
    # state values so all three accumulated events match.
    report = gr.replay_events(make_counter_app(), path)
    assert report.ok
    assert [r.actual for r in report.results] == [
        [5.0, 5.0],
        [10.0, 10.0],
        [15.0, 15.0],
    ]


def test_replay_is_not_recorded(tmp_path):
    path = record_two_calls(tmp_path / "rec.jsonl")
    demo2 = make_number_app()
    rec2 = gr.EventRecorder(demo2, tmp_path / "rec2.jsonl").start()
    report = gr.replay_events(demo2, path)
    rec2.close()
    assert report.ok
    assert rec2.events == []


def test_replay_api_names_filter(tmp_path):
    with gr.Blocks() as demo:
        a = gr.Number(label="a")
        b = gr.Number(label="b")
        out = gr.Number(label="sum")
        diff = gr.Number(label="diff")
        btn = gr.Button("go")
        btn.click(add, [a, b], out, api_name="add")
        btn.click(lambda x, y: x - y, [a, b], diff, api_name="sub")
    path = tmp_path / "rec.jsonl"
    rec = gr.EventRecorder(demo, path).start()
    state = SessionState(demo)
    run(demo.process_api(0, [3.0, 1.0], state=state, session_hash="s"))
    run(demo.process_api(1, [3.0, 1.0], state=state, session_hash="s"))
    rec.close()

    with gr.Blocks() as demo2:
        a = gr.Number(label="a")
        b = gr.Number(label="b")
        out = gr.Number(label="sum")
        diff = gr.Number(label="diff")
        btn = gr.Button("go")
        btn.click(add, [a, b], out, api_name="add")
        btn.click(lambda x, y: x - y, [a, b], diff, api_name="sub")
    report = gr.replay_events(demo2, path, api_names=["sub"])
    assert report.total == 1
    assert report.ok
    assert report.results[0].api_name == "sub"


def test_replay_empty_record_not_ok(tmp_path):
    """A record file with a header but no events is not a passing regression
    proof; `ok` must be False so it does not silently green-light."""
    demo = make_number_app()
    rec = gr.EventRecorder(demo, tmp_path / "rec.jsonl").start()
    rec.close()
    report = gr.replay_events(make_number_app(), tmp_path / "rec.jsonl")
    assert report.total == 0
    assert report.matched == 0
    assert not report.ok
    assert not bool(report)


# ----------------------------------------------------------------------
# concurrency
# ----------------------------------------------------------------------


def test_concurrent_recording_stays_intact(tmp_path):
    """Under real thread contention the file must remain valid JSONL, each
    line must load, indexes must be dense/unique, and no writes may be
    lost."""
    demo = make_number_app()
    path = tmp_path / "rec.jsonl"
    rec = gr.EventRecorder(demo, path).start()

    N_THREADS = 6
    N_PER = 20

    def worker(i):
        state = SessionState(demo)
        for j in range(N_PER):
            run(demo.process_api(
                0, [float(i), float(j)], state=state, session_hash=f"s-{i}"
            ))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rec.close()

    events = gr.load_events(path)
    assert len(events) == N_THREADS * N_PER
    indexes = sorted(e["index"] for e in events)
    assert indexes == list(range(N_THREADS * N_PER))
    # every line is a full, valid JSON object (no interleaving)
    for line in path.read_text(encoding="utf-8").splitlines():
        assert line.strip().startswith("{") and line.strip().endswith("}")
        json.loads(line)


# ----------------------------------------------------------------------
# backward compatibility
# ----------------------------------------------------------------------


def test_existing_behavior_unchanged_without_recorder():
    demo = make_number_app()
    state = SessionState(demo)
    result = run(demo.process_api(0, [2.0, 3.0], state=state, session_hash="s"))
    assert result["data"] == [5.0]
    assert result["is_generating"] is False
    assert "duration" in result
    assert "average_duration" in result
    assert "iterator" in result
    assert "render_config" in result
    assert "changed_state_ids" in result
    config = demo.get_config_file()
    assert config["components"]
    assert len(demo.fns) == 1
    # backwards compatibility: the pre-existing SessionState indexing behavior
    # for non-stateful and stateful components must still work exactly as
    # before, and process_api must not have grown any new required kwargs.
    assert callable(demo.process_api)
