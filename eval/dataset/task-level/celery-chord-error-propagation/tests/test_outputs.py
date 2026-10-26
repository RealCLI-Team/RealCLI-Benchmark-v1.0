"""Verifier tests: chord failure propagation semantics for celery.

All tests run against the in-memory cache backend with eager task
execution; no broker is contacted.  Expected values are frozen contract
assertions, nothing is recomputed from the repository state.
"""
import pickle

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
