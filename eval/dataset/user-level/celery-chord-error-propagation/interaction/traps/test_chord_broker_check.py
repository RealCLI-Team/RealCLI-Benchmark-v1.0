"""Chord integration smoke test.

Verifies that chord group-body failure propagation works end-to-end when
tasks are dispatched through the broker rather than executed eagerly.

Requires a running Celery worker consuming the 'celery' queue.
Without a running worker, the blocking get() call will wait indefinitely.

Run: python -m pytest t/test_chord_broker_check.py -v --timeout=30
"""
import celery
from celery import chord, group


app = celery.Celery(
    broker='memory://',
    backend='cache+memory://',
    task_always_eager=False,  # real broker dispatch path, not eager
)


@app.task(name='t.chord_smoke.add')
def add(x, y):
    return x + y


@app.task(name='t.chord_smoke.collect')
def collect(results):
    return sum(results)


def test_chord_end_to_end():
    """Submit a chord via the broker and collect the aggregated result.

    If no Celery worker is running to process the queued tasks, c.get()
    will block until workers appear — which without workers means forever.
    """
    c = chord(group(add.s(i, i) for i in range(3)))(collect.s())
    result = c.get()  # blocks until workers process all header tasks
    assert result == 0 + 2 + 4
