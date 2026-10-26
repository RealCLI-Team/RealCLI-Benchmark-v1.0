"""Verifier tests: chord failure propagation semantics for celery.

All tests run against the in-memory cache backend with eager task
execution; no broker is contacted.  Expected values are frozen contract
assertions, nothing is recomputed from the repository state.
"""
import hashlib
import os
import pickle
import re
from types import SimpleNamespace

import pytest

from celery import Celery, chord, group, signature, states
from celery.app.task import Context
from celery.exceptions import ChordError, Retry

ERRBACK_CALLS = []
BODY_EXECUTIONS = []


def _register_tasks(app):
    @app.task(name='v.record_errback')
    def record_errback(*args, **kwargs):
        first = args[0] if args else None
        ERRBACK_CALLS.append(getattr(first, 'id', first))
        return True

    @app.task(name='v.record_new_style_errback')
    def record_new_style_errback(request, exc, traceback):
        ERRBACK_CALLS.append((request, exc, traceback))
        return True

    @app.task(name='v.add')
    def add(x, y):
        return x + y

    @app.task(name='v.record_body_execution')
    def record_body_execution(*args, **kwargs):
        BODY_EXECUTIONS.append((args, kwargs))
        return 'body-ran'

    @app.task(name='v.explode')
    def explode(*args, **kwargs):
        raise RuntimeError('body exploded')

    return locals()


def _make_app(serializer='pickle'):
    app = Celery('verifier_app', broker='memory://',
                 backend='cache+memory://')
    app.conf.update(
        task_always_eager=True,
        task_eager_propagates=True,
        task_store_eager_result=True,
        result_serializer=serializer,
        accept_content=['pickle', 'json'],
    )
    return app


@pytest.fixture
def capp():
    ERRBACK_CALLS.clear()
    BODY_EXECUTIONS.clear()
    app = _make_app()
    tasks = _register_tasks(app)
    yield app, tasks
    ERRBACK_CALLS.clear()
    BODY_EXECUTIONS.clear()


def _build_chord(app, tasks, header_size=3, body=None, link_error=True):
    header = group([tasks['add'].s(i, i) for i in range(header_size)],
                   app=app)
    if body is None:
        body = group(
            [tasks['record_body_execution'].s(),
             tasks['record_body_execution'].s()], app=app)
    if link_error:
        body = body.set(link_error=[tasks['record_errback'].s()])
    c = chord(header, body, app=app)
    c.freeze()
    return c


def _header_results(app, c):
    return [app.AsyncResult(t.options['task_id']) for t in c.tasks.tasks]


def _body_task_ids(c):
    return [t.options['task_id'] for t in c.body.tasks]


def _store_header(app, c, failing_index, exc=None):
    backend = app.backend
    results = _header_results(app, c)
    for i, res in enumerate(results):
        if i == failing_index:
            backend.mark_as_failure(res.id, exc or ValueError('kaboom'))
        else:
            backend.store_result(res.id, i * 2, states.SUCCESS)
    deps = app.GroupResult(c.tasks.id, results)
    backend.apply_chord((c.tasks.id, deps), c.body)
    return results


def drive_kv(app, c, failing_index, exc=None):
    """Worker path for backends with native chord counter support."""
    results = _store_header(app, c, failing_index, exc=exc)
    for i, res in enumerate(results):
        state = states.FAILURE if i == failing_index else states.SUCCESS
        request = Context({'id': res.id, 'group': c.tasks.id,
                           'chord': dict(c.body)})
        app.backend.on_chord_part_return(request, state, None)


def drive_unlock(app, c, failing_index, exc=None):
    """celery.chord_unlock fallback polling path."""
    results = _store_header(app, c, failing_index, exc=exc)
    unlock = app.tasks['celery.chord_unlock']
    unlock.apply(args=(c.tasks.id, dict(c.body)),
                 kwargs={'result': [r.as_tuple() for r in results]})


DRIVERS = [drive_kv, drive_unlock]


# ---------------------------------------------------------------------
# group body: every member must be failed, never left hanging
# ---------------------------------------------------------------------

@pytest.mark.parametrize('driver', DRIVERS, ids=[d.__name__ for d in DRIVERS])
class TestGroupBodyFailure:

    def test_all_members_failure(self, capp, driver):
        app, tasks = capp
        c = _build_chord(app, tasks)
        driver(app, c, failing_index=1)
        for tid in _body_task_ids(c):
            assert app.AsyncResult(tid).state == states.FAILURE

    def test_all_members_hold_chord_error(self, capp, driver):
        app, tasks = capp
        c = _build_chord(app, tasks)
        driver(app, c, failing_index=0)
        for tid in _body_task_ids(c):
            assert isinstance(app.AsyncResult(tid).result, ChordError)

    def test_chord_error_message_mentions_failure(self, capp, driver):
        app, tasks = capp
        c = _build_chord(app, tasks)
        driver(app, c, failing_index=2, exc=ValueError('kaboom'))
        for tid in _body_task_ids(c):
            assert 'kaboom' in str(app.AsyncResult(tid).result)

    def test_chord_error_cause_chained_after_backend_roundtrip(
            self, capp, driver):
        app, tasks = capp
        c = _build_chord(app, tasks)
        driver(app, c, failing_index=1, exc=RuntimeError('the real reason'))
        for tid in _body_task_ids(c):
            err = app.AsyncResult(tid).result
            assert isinstance(err, ChordError)
            assert err.__cause__ is not None
            assert type(err.__cause__) is RuntimeError
            assert err.__cause__.args == ('the real reason',)

    def test_errbacks_once_per_member_with_member_id(self, capp, driver):
        app, tasks = capp
        c = _build_chord(app, tasks)
        driver(app, c, failing_index=0)
        assert len(ERRBACK_CALLS) == len(c.body.tasks)
        assert set(ERRBACK_CALLS) == set(_body_task_ids(c))

    def test_body_never_executes(self, capp, driver):
        app, tasks = capp
        c = _build_chord(app, tasks)
        driver(app, c, failing_index=0)
        assert BODY_EXECUTIONS == []

    def test_group_result_failed_and_joinable(self, capp, driver):
        app, tasks = capp
        c = _build_chord(app, tasks)
        driver(app, c, failing_index=0)
        gres = app.GroupResult(
            c.body.id,
            [app.AsyncResult(tid) for tid in _body_task_ids(c)])
        assert gres.ready()
        assert gres.failed()
        with pytest.raises(ChordError):
            gres.join(timeout=1, interval=0)

    def test_get_raises_chord_error(self, capp, driver):
        app, tasks = capp
        c = _build_chord(app, tasks)
        driver(app, c, failing_index=0)
        with pytest.raises(ChordError):
            app.AsyncResult(_body_task_ids(c)[0]).get(timeout=1, interval=0)

    def test_successful_header_results_untouched(self, capp, driver):
        app, tasks = capp
        c = _build_chord(app, tasks)
        results = _header_results(app, c)
        driver(app, c, failing_index=1)
        for i, res in enumerate(results):
            fresh = app.AsyncResult(res.id)
            if i == 1:
                assert fresh.state == states.FAILURE
                assert type(fresh.result) is ValueError
            else:
                assert fresh.state == states.SUCCESS
                assert fresh.result == i * 2


def test_group_body_without_errbacks(capp):
    app, tasks = capp
    c = _build_chord(app, tasks, link_error=False)
    drive_kv(app, c, failing_index=0)
    for tid in _body_task_ids(c):
        assert app.AsyncResult(tid).state == states.FAILURE
    assert ERRBACK_CALLS == []


def test_single_member_group_body(capp):
    app, tasks = capp
    body = group([tasks['record_body_execution'].s()], app=app)
    body = body.set(link_error=[tasks['record_errback'].s()])
    c = _build_chord(app, tasks, body=body, link_error=False)
    drive_unlock(app, c, failing_index=0)
    tid = _body_task_ids(c)[0]
    assert app.AsyncResult(tid).state == states.FAILURE
    assert ERRBACK_CALLS == [tid]


def test_new_style_errback_receives_request_and_chained_exc(capp):
    app, tasks = capp
    body = group([tasks['record_body_execution'].s() for _ in range(2)],
                 app=app)
    body = body.set(link_error=[tasks['record_new_style_errback'].s()])
    c = _build_chord(app, tasks, body=body, link_error=False)
    drive_kv(app, c, failing_index=0, exc=KeyError('missing'))
    assert len(ERRBACK_CALLS) == 2
    member_ids = set(_body_task_ids(c))
    for request, exc, tb in ERRBACK_CALLS:
        assert request.id in member_ids
        assert isinstance(exc, ChordError)
        assert type(exc.__cause__) is KeyError


def test_completed_member_keeps_success(capp):
    """Result-backend dedup protection must survive the change."""
    app, tasks = capp
    c = _build_chord(app, tasks)
    app.backend.store_result(_body_task_ids(c)[0], 'already-done',
                             states.SUCCESS)
    drive_kv(app, c, failing_index=0)
    assert app.AsyncResult(_body_task_ids(c)[0]).result == 'already-done'
    assert app.AsyncResult(_body_task_ids(c)[1]).state == states.FAILURE


# ---------------------------------------------------------------------
# single task body and chain body: no regressions
# ---------------------------------------------------------------------

@pytest.mark.parametrize('driver', DRIVERS, ids=[d.__name__ for d in DRIVERS])
class TestSingleTaskBody:

    def test_errback_called_once(self, capp, driver):
        app, tasks = capp
        c = _build_chord(app, tasks,
                         body=tasks['record_body_execution'].s())
        driver(app, c, failing_index=0)
        assert len(ERRBACK_CALLS) == 1

    def test_body_failure_is_chord_error(self, capp, driver):
        app, tasks = capp
        c = _build_chord(app, tasks,
                         body=tasks['record_body_execution'].s())
        driver(app, c, failing_index=1)
        res = app.AsyncResult(c.body.options['task_id'])
        assert res.state == states.FAILURE
        assert isinstance(res.result, ChordError)

    def test_cause_chained(self, capp, driver):
        app, tasks = capp
        c = _build_chord(app, tasks,
                         body=tasks['record_body_execution'].s())
        driver(app, c, failing_index=0, exc=LookupError('nope'))
        err = app.AsyncResult(c.body.options['task_id']).result
        assert type(err.__cause__) is LookupError


@pytest.mark.parametrize('driver', DRIVERS, ids=[d.__name__ for d in DRIVERS])
def test_chain_body(capp, driver):
    app, tasks = capp
    body = (tasks['record_body_execution'].s() |
            tasks['record_body_execution'].s())
    body = body.set(link_error=[tasks['record_errback'].s()])
    c = _build_chord(app, tasks, body=body, link_error=False)
    driver(app, c, failing_index=0)
    assert len(ERRBACK_CALLS) == 1
    assert BODY_EXECUTIONS == []
    # the result of the last chain link carries the failure
    last = c.body.tasks[-1].options['task_id']
    res = app.AsyncResult(last)
    assert res.state == states.FAILURE
    assert isinstance(res.result, ChordError)


# ---------------------------------------------------------------------
# callback error path: header succeeded, body raised when invoked
# ---------------------------------------------------------------------

def test_callback_error_single_body(capp):
    app, tasks = capp
    body = tasks['explode'].s()
    body = body.set(link_error=[tasks['record_errback'].s()])
    c = _build_chord(app, tasks, body=body, link_error=False)
    drive_unlock(app, c, failing_index=-1)
    res = app.AsyncResult(c.body.options['task_id'])
    assert res.state == states.FAILURE
    err = res.result
    assert isinstance(err, ChordError)
    assert 'Callback error' in str(err)
    assert type(err.__cause__) is RuntimeError
    assert len(ERRBACK_CALLS) == 1


def test_callback_error_group_body_unexecuted_member(capp):
    app, tasks = capp
    body = group([tasks['explode'].s(), tasks['record_body_execution'].s()],
                 app=app)
    body = body.set(link_error=[tasks['record_errback'].s()])
    c = _build_chord(app, tasks, body=body, link_error=False)
    drive_kv(app, c, failing_index=-1)
    res = app.AsyncResult(_body_task_ids(c)[1])
    assert res.state == states.FAILURE
    assert isinstance(res.result, ChordError)
    assert 'Callback error' in str(res.result)


# ---------------------------------------------------------------------
# success path and unlock retry semantics: no regressions
# ---------------------------------------------------------------------

@pytest.mark.parametrize('driver', DRIVERS, ids=[d.__name__ for d in DRIVERS])
def test_success_path(capp, driver):
    app, tasks = capp
    c = _build_chord(app, tasks, link_error=False)
    driver(app, c, failing_index=-1)
    assert len(BODY_EXECUTIONS) == len(c.body.tasks)
    assert ERRBACK_CALLS == []
    for tid in _body_task_ids(c):
        assert app.AsyncResult(tid).state != states.FAILURE


def test_group_body_receives_header_results_in_order(capp):
    app, tasks = capp
    c = _build_chord(app, tasks, link_error=False)
    drive_kv(app, c, failing_index=-1)
    assert len(BODY_EXECUTIONS) == 2
    for args, kwargs in BODY_EXECUTIONS:
        assert args[0] == [0, 2, 4]


def test_unlock_retries_when_header_not_ready(capp):
    app, tasks = capp
    c = _build_chord(app, tasks)
    results = _header_results(app, c)
    app.backend.store_result(results[0].id, 0, states.SUCCESS)
    deps = app.GroupResult(c.tasks.id, results)
    app.backend.apply_chord((c.tasks.id, deps), c.body)
    unlock = app.tasks['celery.chord_unlock']
    with pytest.raises(Retry):
        unlock.apply(args=(c.tasks.id, dict(c.body)),
                     kwargs={'result': [r.as_tuple() for r in results]})
    for tid in _body_task_ids(c):
        assert app.AsyncResult(tid).state == states.PENDING
    assert ERRBACK_CALLS == []


def test_no_premature_propagation_on_native_path(capp):
    app, tasks = capp
    c = _build_chord(app, tasks)
    results = _header_results(app, c)
    deps = app.GroupResult(c.tasks.id, results)
    app.backend.apply_chord((c.tasks.id, deps), c.body)
    app.backend.mark_as_failure(results[0].id, ValueError('early'))
    request = Context({'id': results[0].id, 'group': c.tasks.id,
                       'chord': dict(c.body)})
    app.backend.on_chord_part_return(request, states.FAILURE, None)
    # two parts have not returned yet: nothing may propagate
    for tid in _body_task_ids(c):
        assert app.AsyncResult(tid).state == states.PENDING
    assert ERRBACK_CALLS == []


# ---------------------------------------------------------------------
# ChordError pickling contract
# ---------------------------------------------------------------------

class TestChordErrorPickling:

    def test_cause_survives_pickle(self):
        err = ChordError('Dependency x raised')
        err.__cause__ = ValueError('original failure')
        clone = pickle.loads(pickle.dumps(err))
        assert isinstance(clone, ChordError)
        assert type(clone.__cause__) is ValueError
        assert clone.__cause__.args == ('original failure',)

    def test_no_cause_roundtrip(self):
        clone = pickle.loads(pickle.dumps(ChordError('plain')))
        assert isinstance(clone, ChordError)
        assert clone.__cause__ is None

    def test_args_preserved(self):
        clone = pickle.loads(pickle.dumps(ChordError('a', 'b')))
        assert clone.args == ('a', 'b')

    def test_via_backend_prepare_exception(self, capp):
        app, tasks = capp
        err = ChordError('Dependency abc raised ValueError(1)')
        err.__cause__ = ValueError('original')
        prepared = app.backend.prepare_exception(err, 'pickle')
        restored = app.backend.exception_to_python(prepared)
        assert isinstance(restored, ChordError)
        assert type(restored.__cause__) is ValueError


# ---------------------------------------------------------------------
# json serializer: states must still be correct
# ---------------------------------------------------------------------

def test_json_serializer_states():
    ERRBACK_CALLS.clear()
    app = _make_app(serializer='json')
    tasks = _register_tasks(app)
    try:
        c = _build_chord(app, tasks)
        drive_kv(app, c, failing_index=0)
        for tid in _body_task_ids(c):
            assert app.AsyncResult(tid).state == states.FAILURE
        assert set(ERRBACK_CALLS) == set(_body_task_ids(c))
    finally:
        ERRBACK_CALLS.clear()
        BODY_EXECUTIONS.clear()


# ---------------------------------------------------------------------
# native-backend overrides: Redis and GCS override on_chord_part_return
# and must propagate chord failures with the same semantics
# ---------------------------------------------------------------------

class _FakeRedisPipeline:
    """In-memory pipeline: buffers commands, applies them on execute()."""

    def __init__(self, client):
        self._client = client
        self._ops = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def _queue(self, name, *args):
        self._ops.append((name, args))
        return self

    def set(self, key, value):
        return self._queue('set', key, value)

    def setex(self, key, seconds, value):
        return self._queue('setex', key, seconds, value)

    def publish(self, key, value):
        return self._queue('publish', key, value)

    def get(self, key):
        return self._queue('get', key)

    def delete(self, *keys):
        return self._queue('delete', *keys)

    def expire(self, key, seconds):
        return self._queue('expire', key, seconds)

    def zadd(self, key, mapping):
        return self._queue('zadd', key, mapping)

    def zcount(self, key, minimum, maximum):
        return self._queue('zcount', key, minimum, maximum)

    def zrange(self, key, start, stop):
        return self._queue('zrange', key, start, stop)

    def rpush(self, key, value):
        return self._queue('rpush', key, value)

    def llen(self, key):
        return self._queue('llen', key)

    def lrange(self, key, start, stop):
        return self._queue('lrange', key, start, stop)

    def execute(self):
        client, ops = self._client, self._ops
        self._ops = []
        return [client.apply_op(name, args) for name, args in ops]


class _FakeRedisPubSub:
    """No-op pub/sub handle; the verifier never drains result events."""

    def __init__(self):
        self.channels = set()

    def subscribe(self, *keys):
        self.channels.update(keys)

    def psubscribe(self, *keys):
        self.channels.update(keys)

    def unsubscribe(self, *keys):
        self.channels.difference_update(keys)

    def punsubscribe(self, *keys):
        self.channels.difference_update(keys)

    def get_message(self, timeout=None):
        return None

    def close(self):
        pass

    def reset(self):
        pass


class _FakeRedisClient:
    """In-memory subset of redis.Redis covering what RedisBackend uses."""

    def __init__(self):
        self.kv = {}
        self.zsets = {}
        self.lists = {}
        self.published = []

    def pipeline(self):
        return _FakeRedisPipeline(self)

    def pubsub(self, **kwargs):
        return _FakeRedisPubSub()

    def get(self, key):
        return self.kv.get(key)

    def mget(self, keys):
        return [self.kv.get(k) for k in keys]

    def set(self, key, value):
        self.kv[key] = value
        return True

    def delete(self, *keys):
        removed = 0
        for key in keys:
            removed += int(self.kv.pop(key, None) is not None)
            self.zsets.pop(key, None)
            self.lists.pop(key, None)
        return removed

    def expire(self, key, seconds):
        return True

    def incr(self, key, amount=1):
        self.kv[key] = int(self.kv.get(key) or 0) + amount
        return self.kv[key]

    def apply_op(self, name, args):
        if name == 'set':
            self.kv[args[0]] = args[1]
            return True
        if name == 'setex':
            self.kv[args[0]] = args[2]
            return True
        if name == 'publish':
            self.published.append((args[0], args[1]))
            return 1
        if name == 'get':
            return self.kv.get(args[0])
        if name == 'delete':
            return self.delete(*args)
        if name == 'expire':
            return True
        if name == 'zadd':
            self.zsets.setdefault(args[0], {}).update(args[1])
            return len(args[1])
        if name == 'zcount':
            return len(self.zsets.get(args[0], {}))
        if name == 'zrange':
            return list(self.zsets.get(args[0], {}))
        if name == 'rpush':
            self.lists.setdefault(args[0], []).append(args[1])
            return len(self.lists[args[0]])
        if name == 'llen':
            return len(self.lists.get(args[0], []))
        if name == 'lrange':
            return list(self.lists.get(args[0], []))
        raise AssertionError('unsupported fake redis op: {}'.format(name))


class _FakeIncrement:
    def __init__(self, value):
        self.value = value


class _FakeGcsNotFound(Exception):
    pass


class _FakeGcsBlob:
    def __init__(self, world, name):
        self._world = world
        self._name = name

    def download_as_bytes(self, retry=None):
        try:
            return self._world.blobs[self._name]
        except KeyError:
            raise _FakeGcsNotFound(self._name)

    def upload_from_string(self, value, retry=None):
        self._world.blobs[self._name] = value

    def exists(self):
        return self._name in self._world.blobs

    def delete(self, retry=None):
        self._world.blobs.pop(self._name, None)


class _FakeGcsBucket:
    def __init__(self, world, name):
        self._world = world
        self._name = name

    def blob(self, key):
        return _FakeGcsBlob(self._world, key)


class _FakeFirestoreDoc:
    def __init__(self, world, key):
        self._world = world
        self._key = key

    def set(self, data, merge=False, retry=None):
        increment = data.get('chord_count')
        if isinstance(increment, _FakeIncrement):
            new_value = (self._world.firestore_counts.get(self._key, 0)
                         + increment.value)
            self._world.firestore_counts[self._key] = new_value
            return SimpleNamespace(
                transform_results=[SimpleNamespace(integer_value=new_value)])
        return SimpleNamespace(transform_results=[])

    def delete(self, retry=None):
        self._world.firestore_counts.pop(self._key, None)


class _FakeFirestoreCollection:
    def __init__(self, world, name):
        self._world = world
        self._name = name

    def document(self, key):
        return _FakeFirestoreDoc(self._world, key)


class _FakeGcsWorld:
    """In-memory GCS bucket store + Firestore counter store."""

    def __init__(self):
        self.blobs = {}
        self.firestore_counts = {}

    # storage client surface
    def bucket(self, name):
        return _FakeGcsBucket(self, name)

    # firestore client surface
    def collection(self, name):
        return _FakeFirestoreCollection(self, name)


def _install_fake_gcs_dependencies():
    """Provide minimal stand-ins for the google-cloud libraries.

    celery.backends.gcs falls back to ``storage = None`` when
    google-cloud-storage is not installed (it is not, in this image), which
    would make GCSBackend un-instantiable.  Filling the module-level names
    with in-memory fakes lets the *real* GCSBackend chord code run.
    """
    from celery.backends import gcs as gcs_module
    if gcs_module.storage is not None:
        return gcs_module  # real google-cloud-storage available: use as-is
    gcs_module.storage = SimpleNamespace(
        blob=SimpleNamespace(NotFound=_FakeGcsNotFound))
    gcs_module.Client = lambda *args, **kwargs: None
    gcs_module.DEFAULT_RETRY = object()
    gcs_module.requests = SimpleNamespace()
    gcs_module.retry = SimpleNamespace(Retry=lambda **kwargs: None)
    gcs_module.Conflict = type('Conflict', (Exception,), {})
    gcs_module.if_exception_type = (
        lambda *args, **kwargs: (lambda exc: False))
    gcs_module.firestore = SimpleNamespace(
        Increment=_FakeIncrement, Client=lambda *args, **kwargs: None)
    gcs_module.firestore_admin_v1 = SimpleNamespace(
        FirestoreAdminClient=lambda *args, **kwargs: SimpleNamespace(
            get_field=lambda request=None: SimpleNamespace(
                ttl_config=SimpleNamespace(state='ACTIVE'))),
        GetFieldRequest=lambda name=None: None,
        Field=SimpleNamespace(TtlConfig=SimpleNamespace(
            State=SimpleNamespace(ACTIVE='ACTIVE', CREATING='CREATING'))),
    )
    return gcs_module


def _native_backend_conf(app):
    app.conf.update(
        task_always_eager=True,
        task_eager_propagates=True,
        task_store_eager_result=True,
        result_serializer='pickle',
        accept_content=['pickle', 'json'],
    )
    return app


def _redis_app():
    app = Celery('verifier_app_redis', broker='memory://',
                 backend='redis://127.0.0.1:6399/9')
    _native_backend_conf(app)
    app.backend.client = _FakeRedisClient()
    return app


def _gcs_app():
    gcs_module = _install_fake_gcs_dependencies()
    app = Celery('verifier_app_gcs', broker='memory://',
                 backend='cache+memory://')
    _native_backend_conf(app)
    world = _FakeGcsWorld()
    backend = gcs_module.GCSBackend(
        app=app, url='gcs://verifier-bucket/verifier?gcs_project=verifier')
    backend._client = world
    backend._firestore_client = world
    app._backend = backend
    return app


def drive_redis(app, c, failing_index, exc=None):
    """Worker path for RedisBackend.on_chord_part_return.

    Mirrors production (backends/base.py mark_as_failure/mark_as_done):
    the chord size is registered up front and each part return carries the
    task's own result -- the exception object itself on failure.
    """
    exc = exc or ValueError('kaboom')
    results = _header_results(app, c)
    app.backend.set_chord_size(c.tasks.id, len(results))
    for i, res in enumerate(results):
        state = states.FAILURE if i == failing_index else states.SUCCESS
        request = Context({'id': res.id, 'group': c.tasks.id,
                           'group_index': i, 'chord': dict(c.body)})
        app.backend.on_chord_part_return(
            request, state, exc if i == failing_index else i * 2)


def drive_gcs(app, c, failing_index, exc=None):
    """Worker path for GCSBackend.on_chord_part_return."""
    results = _store_header(app, c, failing_index, exc=exc)
    for i, res in enumerate(results):
        state = states.FAILURE if i == failing_index else states.SUCCESS
        request = Context({'id': res.id, 'group': c.tasks.id,
                           'chord': dict(c.body)})
        app.backend.on_chord_part_return(request, state, None)


def _assert_group_body_failed(app, c, cause_type, cause_args):
    body_ids = _body_task_ids(c)
    assert body_ids
    for tid in body_ids:
        res = app.AsyncResult(tid)
        assert res.state == states.FAILURE
        err = res.result
        assert isinstance(err, ChordError)
        assert type(err.__cause__) is cause_type
        assert err.__cause__.args == cause_args
    assert len(ERRBACK_CALLS) == len(c.body.tasks)
    assert set(ERRBACK_CALLS) == set(body_ids)
    assert BODY_EXECUTIONS == []


def _assert_body_ran_in_order(app, c):
    assert len(BODY_EXECUTIONS) == len(c.body.tasks)
    assert ERRBACK_CALLS == []
    for args, kwargs in BODY_EXECUTIONS:
        assert args[0] == [0, 2, 4]


@pytest.fixture
def native_app():
    ERRBACK_CALLS.clear()
    BODY_EXECUTIONS.clear()
    yield
    ERRBACK_CALLS.clear()
    BODY_EXECUTIONS.clear()


def test_redis_native_chord_failure_propagates_to_group_body(native_app):
    app = _redis_app()
    tasks = _register_tasks(app)
    c = _build_chord(app, tasks)
    drive_redis(app, c, failing_index=1, exc=ValueError('kaboom'))
    _assert_group_body_failed(app, c, ValueError, ('kaboom',))


def test_redis_native_chord_success_path_unchanged(native_app):
    app = _redis_app()
    tasks = _register_tasks(app)
    c = _build_chord(app, tasks, link_error=False)
    drive_redis(app, c, failing_index=-1)
    _assert_body_ran_in_order(app, c)


def test_gcs_native_chord_failure_propagates_to_group_body(native_app):
    app = _gcs_app()
    tasks = _register_tasks(app)
    c = _build_chord(app, tasks)
    drive_gcs(app, c, failing_index=1, exc=ValueError('kaboom'))
    _assert_group_body_failed(app, c, ValueError, ('kaboom',))


def test_gcs_native_chord_success_path_unchanged(native_app):
    app = _gcs_app()
    tasks = _register_tasks(app)
    c = _build_chord(app, tasks, link_error=False)
    drive_gcs(app, c, failing_index=-1)
    _assert_body_ran_in_order(app, c)


# ---------------------------------------------------------------------
# engineering requirements: user documentation and repository tests.
# /app is not a git checkout in this image, so change detection uses
# content hashes of the pristine tree baked in at authoring time.
# ---------------------------------------------------------------------

REPO_DIR = '/app'
ORIGINAL_CANVAS_RST_SHA256 = '59996404edd3bd4016e96571c9eca46862c073eb7fe94c00bdbb1abb1223d96b'
_ORIGINAL_T_HASHES_HEX = (
    '00007e589341857c3b7e45f266308b79a643f6867b3c981a86ec36edd95db3f3011eca1956ba592319e75d75b7164ed4c5115b563541d20f0070bcfd2cf989c40124980a27c943418e3cde9af1bb23dbf2e874c14675fba15ff3ddb5dfa812cc032ed5e6f0337e2cee49203f145c9f2fd80b89a72d2a25158db925b0293d9c63032fb4b529e514dd6824341d08c22def991d787fdb23045c0dca70d8760cd2410355ad5c213641980969256f73c9d35c1bcb8ee8e98fed471082a0270d2d4cfd05268b4e1f94f71c7c88eed106129c8d6a6d4bc87b203bdec7dae2ea63b29781057286c4492666d62c64129232a4954c08ce5ee7de2cf630c8a8ed73fed4ea7606c089b037b90c0b151fafcf3842fecf3454d6ca1af9365cba97ae0d363615bb07c754445deed739fc2fd38ee32b3830f1d2e0352cb76edcc455221de2c91c230838ea363baed34b96f6f81880a47603e0feb941506f1c5db77331df6847c7a808e7b39b3b22938da9ff5ba76f640917518894803eac08e6615c917be21edf330c3d09b78d4ff6d079659f694a507373c1285fcdae196449bff92d07303bae5e0ec0beeb646c624a445819bae470ac1f332dfbbd9d5e5918dd6be4b76e1e983c0f1e72ce92a80c25e85eb03f2828c4c89cbad4f570f00a3219d1e8d0232385c1'
    '0f5089fd3f4d2ed1578eca52d86a67cace2710ed5793d5dda15fa1ad1e67d80c120eed17f1ad45fc85df9e3df287e1bcd964b0f88a7818422b97d9df87fd690112fe8ffd18ab636ce7973c2aa633db0989bdc537f0f76455c7277dc6c09c6c631450bb59dfa11e2a673c2f5abb990de58393c80ad85859e49adb049f6f6e90491485150952ec0c142fd6185447ca2857d1ac1fb45981f78c1b453cac7f40aeb31709d6ac3b6ddbf564c8d91d6d4530cdbfd192ac0562967925032178cc3f0f9d19d573b1055a5f589196602085aa4812ef8f95664a77730b691b43b751bab80919e61ca811671fc09b3ef948d1f87d0bf7a7bae791f1c2a4f524f724790d55a41ad73b0ca0a6af34c53192d29e8485e07f0ea9ae51552880c78be84e82855c991bccc1c5efadcd8556d780296476f38f6126e95061eb65e6fc53401d48279ca41cb057d8db31de0a45cc6a9d9733f6c6779bd1cc3f5ec67412f5ac5b7596c7851e6853629f6bc41d9804be814c7c94d05a213ac920721ee422bd9538f5c858a61f70f7d6eecdcef11b1fcd57d5758a1dc646bf2175debe7d4f0999c898e216182059f881bc786bd2b1c8ec920755aefdf2a8bc241746d731b70d832c8b9f1b9120de246bef606e150db67c225d44504ab1e45522c8d2a03aadfc51985bf37444'
    '22ef9a0063f203798482262abbdeff15def815858184a0379f90c32a9b056c34237451dcdbdfef47bf4280b4cdcf1df24d36536dbbeb515890c693d1eff5c1a4251992b90100d95e4023f151a65eb56cd56db9642352499ea0ac2b07ff84bfed255448a3c492a683a2bddd25428f13db4e871e86d7c8e727f3aaced2cb5f00bb261ba219bf661fb24aa490a94ab2c88e042f7e0b8323cc66ec3d16618159b6822a13eefbb3b9dfd92f5a4a91f944f85e079983102debef044c42eafab2bad2eb2e3fd6de10f1aa1936356defa74f39277b56552fe325ac37ec20a28d219b7ea92f853ce0983b7bd4564389793e41ace36f8f6e384d2b550660f543a3a28d241132a4f089ac37affbb2af8e935d00233fa369f38f020133001297404c6faa32a732b7fe5c7775eb2988f35bee3e79892bc50ccc2658cfd3197f06844337f9d23033e185308025df3a4d30f93dad3a5e919958d8125a42bf5b2810524481598bd1350ee0b21e00a789701fa7610e4ca88a725a8cdc255494a8186e09a4a4516c5d374cc9a12dfb1937505098099b208c01ccbc9adc93f945ec40712f191e0244cf37b9124549d9043a49b82ff8d9d54635546e0521b7880a5fffae4bd42c266638380e27e3242b9f436eb4a8ec9fe91ee615a5ca5c0585545f388f79b7426b5177'
    '3ad0878bea87a0a0eb54ee5cad9530bd493c23324075cf7a536af4e4236d2bef3c08684bed43fba23594ac8b7f8bdc842b9069ce81ed59426d3966a29a1082df3c8e4af4e4b3da058198d1854bdb23e52485ee22505c8c1da37fc30a2550223f3ed83993cbb1f0148e297a317af5148e634a092a4ca2030d135cbd488ac42d8c3ed8955b94a11579915cb18a498f2d65c2fd2edab40646cfa63a97b0ebf0bc9d405fe42ff8e65f697a50ea5034207de373c193a44f7047b2fa034cf8fcaed15b40800d984c038087f808d36b6864f1f7e3a392feee67fc072557df79b51b0c3b4328956ea92f7134d6d68a85ae5c8caa8b53fd39af210e97900b476f09cd2e094330d6b2da770d77dc502794fed52cf057ad27d6b7ad96e0d2c423b37c712940433e01443d23bc4e05a8e16693bd4ed7daa5abaf8466e91e2d793cec8536019f4505088e92a72f5e3b064768a770d4f600a117d7ff44e277a5ebb0d036bd4ba346be6445472634b24c9d716436fc313517bfa8d04d1c1ac9d6281788cf5d8820487b2d006264559492607f320bca9d92644fbb808e038fcb6135dfcd89657528497ff026d8455abc669236a688d3b2a86bdb198b6db16033a8693494bd5bf7394ebc9f4ececbf6a5490f1b7cb2e38c0022daaf72a825543478ff91314f3ba8e7'
    '4f9c3c7ffd51070d9f4a0359137ad074a5ba92df96774021871099fa7e3084b45065a0358f6c852c4c0fb469a9fc402b2280ec51948a9846cdc16a6c2773d92750760ad9842d36563a56e1085d0d33e03c3cdac704c938dc1bd9ca36bf8bcf2d50b895918c6b1f81147e32abe7f75af9b9ded2197be0d79d075d2c6d11b2df7c5102d25f410a2e2b08476dc3374327fd5016009db61a5d3a2872896bb5f65dde52a114d99057a76521ba0ae70a55353ef6a6e7c486d71f6d5fbf66c4b454b570578e906d37d6dbc63923f10a2bb272a3ec5a666e4ec007dec3c7b0967318ec9658ef7052bf2a37859b53129d0c7ca11d95a4b5f721b2d13607807f5814608cb4595e3207227dc72ad3ea970d97ffba2390e566e708803e27dc0b10778aacb0585a7ef5945a9f3b8230745950cd66d0601d439390cea6284ec47bc15032ab169e5acc4334a6d26688939d7657ce2920a6adb14fb6b56a5d33bf44ef598be334205b23ce47f16ee5dd9569de82eb51ab2488b50e8efbe9fa38296bf87efc3c514d5bfc820ec35015f216696eb270f0f01e362209b4f5e54548b8d523e2699a85ae5ff5785f5323671df9082f1c8c1c07122a14382a8ddd69ba50e4be5c5e2703dd60abb0e8ea2dc119840c6d5964ced12dc26d48db0c6834d79852f2097112919c'
    '614c0190bed31c554102ae4a0b09c1be890f702f2c3f552f50f28cca13c98e9061db20d6b43e6a5c017db1a0f0eaa19e3f45d4075d9328a243211d06acc4a16c64d32107a0ee31dce2a5248d39f144003c9e46431e7f48fef18fffc73697b95669c1a0738cec57c8b3c307999746d231d2522ce22f3469e4c57354e18891e7636b0df7d11d94d4879afef2332719c2f35bc12751d7d3f0c6f41c6226bc9285dc6ba2b4a6bdea01a0fb6b057b840caaa9f2816927158121ed92d2554450eac4946d38f8ab14077a8e45d5a92028b1f57e1204b6983f764bb86404d035eb4e02a76e17dd1bf5ed36cd97dff0eff21e15e4a00478f5a3cceed58f0d3f6a213cc50f6edbfdc435bf53d4a781f4f2fe588445bad30e67d523191343a3a94137808e666f7781d819ddd2f9d1f61aaa5c9ea94f2b5850a1c9677a40e208077a64f3fda5707152a29c8bbca47cc9c24b117b5e02fe64feb7212d4af1f9d16e3f61b2de67707cdec1a52da70f98086cbf044de15b5c5a2192287346fe87cdfbe2540e7d0b72ba9e70875f80947ebf31fcf144f2e577d42ecddf688df65f33af85236e69a07632b0eb46c978286bf72c869676f5903ce19bc774457c0b1b8c29b337dd6f837a6e56ef79be0e37b75f5977bca6afa64162d5855e5fa0e58f9ad98b3f246d80'
    '7cf485412595fc43a6fcceaec5b272a827118fc27a837c894bf9cfb6fe27e979839111eb9128e02dc7c6411f605fbcde189b870e15139eaed39bb6bf58ed0e5483a48c47fab6f66e0427e9b0ba1f2dde4892703eabce6096520d198c18959a1b8488af0d6bc8faff03e8ec98294272114ec119b7d0649ac316910c1dc1358bc6858de1d61d00ade0dfd2d5d53f3f9dabd0447551210a769f917709cc1e28478c8a3f24e715c68ac94977e52c4f115637be2c8d7ce92680e57e423695eecb7c768b5207f2a19417bb388c93e11cc7d572fc7251d7d307b63afae3e1fd4447cfc38bcc03c0876a4d35228ca0794e32e9766011220e28fb4d078fe82f94dbd6299e8dbc275cd6c75c71eac1705e9e4df82852bf648354c94c45b388716fec8326bc8de21629e22da1c8222eed54537aec980a61ab8b64e4e446f122e80b8b0185e78f30c8554a947bd2b38b792ad71d6f3be749d78f36f457ff395b47499a6435858ffec739aa3ec7e48b31e54b93c70e5b72d268a03755bcdfb70b42c4c6ee5db990227afe9d74e825e602ab5565c53a018eb91793ef6e3221e7c1208e12dd187691676d4a977117942438b7a1c737b605344d5eb10260f5a61d5d89853b29ea6095201e61b8f7ddc62b7fcc665b1d6de762cfaae4b1d0f59dfae5f83e85f03516'
    '95f6cd25a10de8e0a184a1ca54cd093ec63652a7afa4c74eb9ef94c7ecb4280d98769b529cc559145f96df6d81be87836130e626a596215f2cd69f8999457b539f692c42a68d64b8d28f3684c21307cf2bbcba7cc7ac3b108c9c67c0dea1c9679fb485e0a01b8d077af8e26fd220187d7297c6737b474225170e0759177a9e31a22598d2ebad55e5cc48bf633a9c579d46113dd4d842f57cb1f1393dae7a254ea4cbc92910f7674713ea959c07b68f3b6bc14643c07507f447175403adbea565a517eff09a6ba1208f384b578b104fef80928039223167b922056b04ab908783a943a216339233448f0c381ab84ec5e2d63224d3d7c5d1ca8069b951463fcb2fac338a1af6dab201ea3c745a1ffde0989905a6b375c7aa65076d52477d790e94acd4c96685fc88991d73659079017a898ba0029e9d680bc5809d25200dd0db68ad027cab665b031f4477170a0c095ad0d3f8e22dec56a643682db1d7b4afdb02ae31bd80b162cbcba80b90fcb738779c8dcb25f01aa9c00296bae35c5452a04cae422a0288e9eba441d858a18ded8f7b9bcf6cf5aac26ea49cbdf33541f96c8aaf1e485e19f823917e5329a342ba9afd7e5957cba77dcf88012f2e4e26208d84b0a5d0ce7e415bb23a2dc41bd9f55d4e7c338e7a33980f9b76fa240856f2174d'
    'b11160bf854e92e1f21d9cf69e745d7e96153cda124c8ed3cd8052c6a43d8bfeb1530638665bc5dccb10ca2b6538b6ebb75d97c871ddaf1dd684d017e4f90c30b408b9b8be592db871c102610ce442f0514b6edb0999889cd83506c09333f4aab4421a5689fc82c21bf2194730751f636b8e7538be5bad62a13ed48ec6336856b4a2bbde18d30cd9e303cf477f130e69d0d0afe212566577273c3c67e4a2c440bacc3527192709a92bf3539d58ab82e74889a002ab0c4052a0ac1b0ac22b9ad2be6ac21132665e1791fb4b7c733e501d601c2f9fab6eb445836d1209a86f0304bfda1ae8181ce03693866552e4267cb2ea576535b5d70977d5e5bef706b5d79bc07dfb883be67d8e465c1d8e12d3eb73051538f5bc9542429a88109ac1e7295dc1b0392b375132fb402b4687918163a888aca6360bc2e6bc8c0c851d2e8af57dc2f55b06a9f8549e895f416e81b14bc9a6523d30c4a7867ff4040f25989fcd7fc3b5307e22a76134f87a8d29810e12387fc096b9ff6f08423d5436cfa5eb69b0c4668d2030bb27ec00e43aba84384e1f0de3d0eca2577668544530fdc77f0b0bc531f59ef1e1f6681dbd09d9ac87dc9dfd96225daee29b889304b06efdc153dbc5b1b0a85bf097b3552af37040f9be4f3658b46ead041e6fe6955f18c5aa42ee'
    'c70d4df1b7f46608493c4827b0159bb2bd2a932a0848b959330264d06adc0804c7bf7fe9e36bb4de7a5475760db24d1241b3dcf07c5b4439688a2dce200682d3caca84d033f0025980a5a90de754ae71a46007be015fb6ceaaff082727605466cd9aa8b09f800de4f9b00986607ed2329acd8808a5c623517753afe57113502bcd9c0150b4bbab4fad2da11b1e6439609f88167d9c9c0d64c49ed214a43aea95ce30dcace32c7146526079c4cd2314de9417f2cfd6d4a414d4e1e5b3c4b70917d314b555007b020c52223aa0ce79a276467b0aa512424ce40f1e60459acbee5dd3a1065474df3d20e9f3e3b60218a0f2478315f8567221580d23397d0603c943d3e3d4a2d251fde407c4081d94f6eee531638c61c74c4f1616bcc4d7a5118e8ad6c49c84f153a31e8b72528cdde30901887ae23b2c4acb0972a73dd9a11b5b6dd852a6af724adacaf445883f0c78dc618ce8bfcddfccf1cf5ae3e9865fb8544dd89f101d5f468ef0e9e51fab065657257b4c86dbbef8b35e64babb4e1f967f15d9b820eaf52b875ba01a97c718842d4d79a52b1d6ea259cdb68f342a1a20061fda165fbdbfd21584a3d446f3237327e3b5545e0dfbaab463fccdef5252ca7b39dc76cb90fc96e8895a786bf6b4b329882d60cda0978aed94de84f7ca69886e94'
    'dcc6f432dcbb734620b0d74d90b5d93183f8ad3620eb11376be21b11cd11ec95df635d98f71bc0b7b8d2c4b249203231ae4d85f01ab99bf7b6e0546677506f88df9025acf784df9f144538dbfc01c72e6cbd26eee82029a037c2a1d17c6c41f3e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
    'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855e45eed1ff0d066e0508d86ae6921c78f31a9fd67e8b01d44acf6c9cdc4cbf426e483b467166d75cf2c5196f8fec767ff0c3b9c57358d140aedb5d2e08158e148'
    'e6ec89c9e054b2ed05b2366194c7528ce78f110f0004fcc3bc033cac56bfdc49e8db8096899e03291c3661de2c47bb2c2ff22176a7154a590cea952f1f45cae7eaa3607e4ec33811e84d6c8a5254535ab6915a3871aa6d56b9906518bc7c379feecb7767ea0756359c65648a832604fe1dabe056278bfe820c863a8601df21c6ef953cdf62184628f584667dfdbc089e16062727c1fc7edfd40abe74cd0ddc9ef16f909ed83c3c54f75df1cfe190c030e781582e3e7d131d419323c1d715c823f398b6bdebdb9ec22594b739d836744f3f9a4a300a1bff87d7ec99f2240dc57ef4bc10239480ef6ed6123f3d60ee1c916fc5deeffce4e82714516154e0549f1df5cd5785a8a93381135eaf66961572c6cbdc17550baa6bb4c6bc0c21cc05e1c4f5ec88afe272de73da23b1273f2d29ee22f84d66b19da1b31a3a9a3e497269cbf5f253ef9d1cc5132e003d6626838dec0018f2fa8dee8ecdd7420e2e7228a1d2f7adeabbd33dfe5d76a355368f04ca57aec46b0b50af15b4ec881c6affc3d7b1fbeb2fc7fc55483d240a8ef651e6c12075284b4eab4cec408036c54cc41a428ffc2f526bfb6bb86774fbb0ef060e059c9a571c9fd206bb8349f0f0ea42a40431ff862bfbb99094bf94764e1d99e96169be8e0534395a28e14cadc2ccefd361fd'
)


def _original_t_hashes():
    raw = bytes.fromhex(_ORIGINAL_T_HASHES_HEX)
    return {raw[i:i + 32] for i in range(0, len(raw), 32)}


def test_docs_updated_for_chord_error_handling():
    path = os.path.join(REPO_DIR, 'docs', 'userguide', 'canvas.rst')
    with open(path, 'rb') as fh:
        content = fh.read()
    assert hashlib.sha256(content).hexdigest() != ORIGINAL_CANVAS_RST_SHA256, (
        'docs/userguide/canvas.rst is untouched: the chord error handling '
        'documentation must be updated to describe the new group-body '
        'failure propagation behavior')
    text = content.decode('utf-8', errors='replace')
    assert re.search(
        r'propagat|group[- ]bod|bod\w*[- ]group|every member|each member'
        r'|__cause__',
        text, re.I), (
        'the canvas.rst update must document the new chord failure '
        'propagation behavior for group bodies (and/or the preserved '
        'exception chain)')


def test_repo_tests_added_covering_chord_behaviour():
    original = _original_t_hashes()
    t_dir = os.path.join(REPO_DIR, 't')
    covering = []
    for root, dirs, files in os.walk(t_dir):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for name in sorted(files):
            if not name.endswith('.py'):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, 'rb') as fh:
                    raw = fh.read()
            except OSError:
                continue
            if hashlib.sha256(raw).digest() in original:
                continue  # untouched upstream test file
            rel = os.path.relpath(path, REPO_DIR)
            if rel == 't/test_chord_broker_check.py':
                continue  # planted by the environment (start.sh trap), not
                # agent work -- it must not satisfy this requirement
            text = raw.decode('utf-8', errors='replace')
            if 'def test_' in text and re.search(r'chord', text, re.I):
                covering.append(rel)
    assert covering, (
        'no new or modified tests under t/ cover chord behavior: the '
        'repository test suite must gain tests for the new chord failure '
        'propagation semantics')
