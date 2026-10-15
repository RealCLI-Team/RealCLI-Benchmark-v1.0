#!/usr/bin/env python3
"""Grade the rolling-update controller the agent was asked to build.

The controller is graded exclusively through its *public* behaviour, driven
against a stand-in Docker Engine.  We never inspect the agent's internal data
structures, label keys or method bodies: we hand it revisions and update
policies, let it talk to a fake ``APIClient`` that faithfully remembers the
containers it is told to create (and reports their health per-image), and then
assert the things the contract promises -- correct batching, idempotency,
blue/green cutover ordering, canary metric gating, automatic rollback on a bad
deploy, explicit rollback and scaling.

To keep the grade honest the git-tracked source tree is restored before the
package is imported, so the only thing under test is the agent's *new*,
untracked ``docker/rollout`` package running on top of pristine docker-py.
"""

from __future__ import annotations

import itertools
import os
import subprocess
import sys

APP = "/app"


# ---------------------------------------------------------------------------
# Harness plumbing
# ---------------------------------------------------------------------------

def _restore_tracked_sources():
    """Revert edits to git-tracked files so only new code is graded.

    The task is to *add* a package, not to change docker-py.  Restoring
    tracked files means the controller cannot be "passed" by weakening the
    library it builds on; the agent's untracked package is left in place.
    """
    try:
        subprocess.run(
            ["git", "checkout", "HEAD", "--", "."],
            cwd=APP,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:  # pragma: no cover - defensive
        print("WARNING: could not restore tracked sources: {}".format(exc))


def _load():
    """Import the agent's rollout package and pull out its public API."""
    sys.path.insert(0, APP)
    import importlib

    try:
        pkg = importlib.import_module("docker.rollout")
    except Exception as exc:
        raise AssertionError(
            "could not import docker.rollout: {}: {}".format(
                type(exc).__name__, exc
            )
        )

    missing = [
        n for n in ("RolloutController", "Revision", "UpdatePolicy")
        if getattr(pkg, n, None) is None
    ]
    if missing:
        raise AssertionError(
            "docker.rollout is missing required names: {}".format(missing)
        )
    return pkg


# ---------------------------------------------------------------------------
# A stateful stand-in for docker.api.APIClient with per-image health
# ---------------------------------------------------------------------------

class FakeEngine:
    """A minimal, faithful Docker Engine simulator for one service.

    It records every container creation together with the image, labels and
    environment the controller asked for, and serves them back through
    ``containers`` so a *fresh* controller -- which keeps no state of its own
    -- can recognise them on a later call.

    Health is modelled *per image*: ``inspect_container`` reports a container
    healthy unless its image is in ``unhealthy_images``.  This is what lets the
    tests distinguish "the new revision came up broken" (only the new image is
    unhealthy) from a globally broken world, and therefore verify that a
    rollback restores the *old*, still-healthy revision.
    """

    def __init__(self):
        self.containers_store = {}   # name -> record
        self.create_kwargs = {}      # name -> create_container kwargs
        self.calls = []
        self._ids = itertools.count(1)
        self.unhealthy_images = set()

    # -- observation -----------------------------------------------------

    def containers(self, quiet=False, all=False, trunc=False, latest=False,
                   since=None, before=None, limit=-1, size=False,
                   filters=None):
        self.calls.append(("containers",))
        # docker-py's APIClient accepts the label filter in several legal
        # shapes: a single "k=v" string, a list of such strings, or a
        # {k: v} / {k: True} mapping. Normalise all of them to a list of
        # (key, value-or-None) conditions and require every one to match,
        # exactly as the real engine does -- a correct agent may pass any of
        # these forms, so the fake must not choke on the non-string ones.
        want = (filters or {}).get("label")
        conds = []
        if want:
            items = want if isinstance(want, (list, tuple)) else (
                ["{}={}".format(k, v) for k, v in want.items()]
                if isinstance(want, dict) else [want])
            for item in items:
                k, sep, v = str(item).partition("=")
                conds.append((k, v if sep else None))
        out = []
        for rec in self.containers_store.values():
            labels = rec.get("Labels", {})
            ok = True
            for k, v in conds:
                if k not in labels or (v is not None and labels.get(k) != v):
                    ok = False
                    break
            if not ok:
                continue
            out.append(dict(rec))
        return out

    def inspect_container(self, container):
        self.calls.append(("inspect_container", container))
        rec = self.containers_store.get(container)
        if rec is None:
            for r in self.containers_store.values():
                if r.get("Id") == container:
                    rec = r
                    break
        if rec is None:
            raise KeyError(container)
        name = rec["Names"][0].lstrip("/")
        kw = self.create_kwargs.get(name, {})
        image = kw.get("image")
        healthy = image not in self.unhealthy_images
        return {
            "Id": rec.get("Id"),
            "Name": rec.get("Names", ["/" + str(container)])[0],
            "State": {
                "Running": True,
                "Status": "running",
                "Health": {"Status": "healthy" if healthy else "unhealthy"},
            },
            "Config": {
                "Image": image,
                "Labels": rec.get("Labels", {}),
                "Env": [
                    "{}={}".format(k, v)
                    for k, v in (kw.get("environment") or {}).items()
                ],
            },
        }

    # -- helper builders -------------------------------------------------

    def create_host_config(self, *args, **kwargs):
        self.calls.append(("create_host_config", kwargs))
        return {"__hostconfig__": kwargs}

    # -- mutation --------------------------------------------------------

    def create_container(self, image=None, **kwargs):
        name = kwargs.get("name")
        cid = "ctr_{}".format(next(self._ids))
        self.calls.append(("create_container", name, image))
        full = dict(kwargs)
        full["image"] = image
        self.create_kwargs[name] = full
        self.containers_store[name] = {
            "Id": cid,
            "Names": ["/" + str(name)],
            "Labels": dict(kwargs.get("labels") or {}),
            "State": "running",
            "Status": "Up",
        }
        return {"Id": cid}

    def start(self, container, *args, **kwargs):
        self.calls.append(("start", container))

    def stop(self, container, *args, **kwargs):
        self.calls.append(("stop", container))

    def remove_container(self, container, *args, **kwargs):
        self.calls.append(("remove_container", container))
        for name, rec in list(self.containers_store.items()):
            if name == container or rec.get("Id") == container:
                del self.containers_store[name]
                self.create_kwargs.pop(name, None)
                break

    # -- convenience for assertions -------------------------------------

    def verbs(self):
        return [c[0] for c in self.calls]

    def images_live(self):
        return {kw.get("image") for kw in self.create_kwargs.values()}

    def count(self):
        return len(self.containers_store)

    def create_order(self):
        return [c[1] for c in self.calls if c[0] == "create_container"]

    def index_of_first(self, verb):
        for i, c in enumerate(self.calls):
            if c[0] == verb:
                return i
        return -1


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _noop(_seconds):
    """A sleeper that never actually sleeps -- keeps health gating fast."""


def _controller(pkg, engine, service="svc", replicas=None,
                metric_provider=None):
    """Construct a RolloutController, tolerating optional kw names."""
    kwargs = dict(sleeper=_noop)
    if replicas is not None:
        kwargs["replicas"] = replicas
    if metric_provider is not None:
        kwargs["metric_provider"] = metric_provider
    try:
        return pkg.RolloutController(engine, service, **kwargs)
    except TypeError:
        # Fall back to a minimal signature.
        return pkg.RolloutController(engine, service)


def _rev(pkg, image, **extra):
    data = {"image": image}
    data.update(extra)
    return pkg.Revision.from_dict(data)


def _policy(pkg, **kwargs):
    return pkg.UpdatePolicy.from_dict(kwargs)


def check_revision_identity(pkg):
    """A revision's identity is content-derived and ordering-independent."""
    a = _rev(pkg, "web:1", environment={"A": "1", "B": "2"})
    b = _rev(pkg, "web:1", environment={"B": "2", "A": "1"})
    if a.revision_id() != b.revision_id():
        raise AssertionError(
            "revision_id must not depend on environment ordering"
        )
    c = _rev(pkg, "web:2", environment={"A": "1", "B": "2"})
    if a.revision_id() == c.revision_id():
        raise AssertionError("revision_id must change when the image changes")
    if a != b or a == c:
        raise AssertionError("Revision equality must follow revision_id")


def check_policy_validation(pkg):
    """Malformed policies/revisions are rejected with the error family."""
    RevisionError = getattr(pkg, "RevisionError", ValueError)

    try:
        pkg.Revision.from_dict({})
    except Exception as exc:
        if not isinstance(exc, RevisionError):
            raise AssertionError(
                "a revision without 'image' must raise RevisionError"
            )
    else:
        raise AssertionError("a revision without 'image' must raise")

    try:
        pkg.UpdatePolicy.from_dict({"strategy": "nonsense"})
    except Exception as exc:
        if not isinstance(exc, RevisionError):
            raise AssertionError("an unknown strategy must raise RevisionError")
    else:
        raise AssertionError("an unknown strategy must raise")

    try:
        pkg.UpdatePolicy.from_dict({"batch_size": 0})
    except Exception as exc:
        if not isinstance(exc, RevisionError):
            raise AssertionError("batch_size < 1 must raise RevisionError")
    else:
        raise AssertionError("batch_size < 1 must raise")


def check_initial_rollout(pkg):
    """A first rollout brings up exactly `replicas` new-revision containers."""
    engine = FakeEngine()
    ctl = _controller(pkg, engine, "api", replicas=4)
    result = ctl.rollout(_rev(pkg, "api:1"), _policy(pkg, batch_size=2))
    if not getattr(result, "ok", False):
        raise AssertionError("initial rollout must succeed")
    if engine.count() != 4:
        raise AssertionError(
            "expected 4 replicas, got {}".format(engine.count())
        )
    if engine.images_live() != {"api:1"}:
        raise AssertionError("all replicas must run the target image")


def check_plan_is_pure_and_idempotent(pkg):
    """plan() must not mutate, and a settled service re-plans to a no-op."""
    engine = FakeEngine()
    ctl = _controller(pkg, engine, "api", replicas=3)
    ctl.rollout(_rev(pkg, "api:1"))

    calls_before = len(engine.calls)
    plan = ctl.plan(_rev(pkg, "api:1"))
    # planning may read state but must not create/remove anything.
    mutating = [c for c in engine.calls[calls_before:]
                if c[0] in ("create_container", "remove_container", "start")]
    if mutating:
        raise AssertionError("plan() must not mutate the engine")
    if not plan.is_noop:
        raise AssertionError(
            "re-planning an already-current service must be a no-op"
        )


def check_rolling_batches(pkg):
    """A rolling update replaces replicas batch_size at a time."""
    engine = FakeEngine()
    ctl = _controller(pkg, engine, "api", replicas=4)
    ctl.rollout(_rev(pkg, "api:1"), _policy(pkg, batch_size=2))

    plan = ctl.plan(_rev(pkg, "api:2"), _policy(pkg, batch_size=2))
    if plan.is_noop:
        raise AssertionError("a version change must not plan a no-op")
    if plan.batch_count() != 2:
        raise AssertionError(
            "4 replicas at batch_size 2 must yield 2 batches, got {}".format(
                plan.batch_count()
            )
        )

    result = ctl.rollout(_rev(pkg, "api:2"), _policy(pkg, batch_size=2))
    if not result.ok:
        raise AssertionError("rolling update must succeed when healthy")
    if engine.images_live() != {"api:2"}:
        raise AssertionError("all replicas must end on the new image")
    if engine.count() != 4:
        raise AssertionError("replica count must be preserved across rollout")


def check_health_triggers_rollback(pkg):
    """A new revision that never becomes healthy is rolled back to the old."""
    engine = FakeEngine()
    ctl = _controller(pkg, engine, "svc", replicas=4)
    ctl.rollout(_rev(pkg, "svc:1"), _policy(pkg, batch_size=2))

    engine.unhealthy_images.add("svc:2")   # the new revision is broken

    RolloutFailed = getattr(pkg, "RolloutFailed", Exception)
    rolled_back = False
    raised = False
    try:
        ctl.rollout(
            _rev(pkg, "svc:2"),
            _policy(pkg, strategy="rolling", batch_size=2,
                    health_attempts=2, auto_rollback=True),
        )
    except RolloutFailed as exc:
        raised = True
        rolled_back = getattr(exc, "rolled_back", False)
    if not raised:
        raise AssertionError("an unhealthy rollout must raise RolloutFailed")
    if not rolled_back:
        raise AssertionError("auto_rollback must set rolled_back on the error")
    if engine.images_live() != {"svc:1"}:
        raise AssertionError(
            "after rollback the service must run ONLY the old revision, "
            "got {}".format(engine.images_live())
        )
    if engine.count() != 4:
        raise AssertionError("rollback must restore the original replica count")


def check_no_auto_rollback(pkg):
    """With auto_rollback off, a failed rollout is NOT reverted."""
    engine = FakeEngine()
    ctl = _controller(pkg, engine, "svc", replicas=2)
    ctl.rollout(_rev(pkg, "svc:1"))
    engine.unhealthy_images.add("svc:2")

    RolloutFailed = getattr(pkg, "RolloutFailed", Exception)
    rolled_back = True
    try:
        ctl.rollout(
            _rev(pkg, "svc:2"),
            _policy(pkg, batch_size=2, health_attempts=2,
                    auto_rollback=False),
        )
    except RolloutFailed as exc:
        rolled_back = getattr(exc, "rolled_back", False)
    if rolled_back:
        raise AssertionError(
            "auto_rollback=False must not report a rollback"
        )


def check_blue_green_ordering(pkg):
    """Blue/green stands up the new set, promotes, THEN retires the old."""
    engine = FakeEngine()
    ctl = _controller(pkg, engine, "web", replicas=2)
    ctl.rollout(_rev(pkg, "web:1"))

    plan = ctl.plan(_rev(pkg, "web:2"), _policy(pkg, strategy="blue_green"))
    kinds = [getattr(s.kind, "value", s.kind) for s in plan.steps]
    if "promote" not in kinds:
        raise AssertionError("a blue/green plan must contain a promote step")
    first_create = kinds.index("create")
    promote = kinds.index("promote")
    last_create = len(kinds) - 1 - kinds[::-1].index("create")
    remove_after_promote = [
        i for i, k in enumerate(kinds) if k == "remove" and i > promote
    ]
    if not (first_create < promote):
        raise AssertionError("creates must precede the promote")
    if last_create > promote:
        raise AssertionError("all creates must happen before promote")
    if not remove_after_promote:
        raise AssertionError("old replicas must be removed after promote")

    result = ctl.rollout(_rev(pkg, "web:2"), _policy(pkg, strategy="blue_green"))
    if not result.ok:
        raise AssertionError("healthy blue/green rollout must succeed")
    if engine.images_live() != {"web:2"}:
        raise AssertionError("blue/green must end with only the new image")
    if engine.count() != 2:
        raise AssertionError("blue/green must end at the desired replica count")


def check_recreate(pkg):
    """Recreate tears everything down before rebuilding."""
    engine = FakeEngine()
    ctl = _controller(pkg, engine, "job", replicas=2)
    ctl.rollout(_rev(pkg, "job:1"))

    plan = ctl.plan(_rev(pkg, "job:2"), _policy(pkg, strategy="recreate"))
    kinds = [getattr(s.kind, "value", s.kind) for s in plan.steps]
    last_remove = len(kinds) - 1 - kinds[::-1].index("remove")
    first_create = kinds.index("create")
    if last_remove > first_create:
        raise AssertionError("recreate must remove all before any create")

    result = ctl.rollout(_rev(pkg, "job:2"), _policy(pkg, strategy="recreate"))
    if not result.ok or engine.images_live() != {"job:2"}:
        raise AssertionError("recreate must end on the new image")


def check_canary_success(pkg):
    """A canary passing its metric gate rolls out to the whole fleet."""
    if getattr(pkg, "CanaryAnalysis", None) is None:
        raise AssertionError("docker.rollout must expose CanaryAnalysis")

    def good_metrics(name, i):
        return {"error_rate": 0.001, "latency_p99": 120}

    engine = FakeEngine()
    ctl = _controller(pkg, engine, "gw", replicas=4,
                      metric_provider=good_metrics)
    ctl.rollout(_rev(pkg, "gw:1"))

    policy = pkg.UpdatePolicy.from_dict({
        "strategy": "canary", "batch_size": 2, "canary_slots": 1,
        "canary": {
            "intervals": 3,
            "metrics": [
                {"name": "error_rate", "threshold": 0.01, "comparison": "lt"},
                {"name": "latency_p99", "threshold": 200, "comparison": "lt"},
            ],
        },
    })
    plan = ctl.plan(_rev(pkg, "gw:2"), policy)
    kinds = [getattr(s.kind, "value", s.kind) for s in plan.steps]
    if "analyze" not in kinds:
        raise AssertionError("a canary plan must contain an analyze step")

    result = ctl.rollout(_rev(pkg, "gw:2"), policy)
    if not result.ok:
        raise AssertionError("a passing canary must complete the rollout")
    if engine.images_live() != {"gw:2"}:
        raise AssertionError("a passing canary must migrate the whole fleet")


def check_canary_rejection_rolls_back(pkg):
    """A canary failing its metric gate is rejected and rolled back."""
    def bad_metrics(name, i):
        return {"error_rate": 0.5, "latency_p99": 900}

    engine = FakeEngine()
    ctl = _controller(pkg, engine, "svc", replicas=4,
                      metric_provider=bad_metrics)
    ctl.rollout(_rev(pkg, "svc:1"))

    policy = pkg.UpdatePolicy.from_dict({
        "strategy": "canary", "batch_size": 2, "canary_slots": 1,
        "auto_rollback": True,
        "canary": {
            "intervals": 3,
            "metrics": [{"name": "error_rate", "threshold": 0.01}],
        },
    })
    RolloutFailed = getattr(pkg, "RolloutFailed", Exception)
    raised = False
    try:
        ctl.rollout(_rev(pkg, "svc:2"), policy)
    except RolloutFailed:
        raised = True
    if not raised:
        raise AssertionError("a failing canary must raise RolloutFailed")
    if engine.images_live() != {"svc:1"}:
        raise AssertionError(
            "a rejected canary must leave the service on the old revision, "
            "got {}".format(engine.images_live())
        )


def check_canary_metric_thresholds(pkg):
    """CanaryAnalysis evaluates comparisons and aborts on a failing interval."""
    Comparison = getattr(pkg, "Comparison", None)
    MetricCheck = getattr(pkg, "MetricCheck", None)
    if Comparison is None or MetricCheck is None:
        raise AssertionError("rollout must expose MetricCheck and Comparison")

    analysis = pkg.CanaryAnalysis.from_dict({
        "intervals": 3,
        "metrics": [{"name": "error_rate", "threshold": 0.05,
                     "comparison": "lt"}],
    })

    # Always-good provider passes.
    good = analysis.run(lambda n, i: {"error_rate": 0.01}, "canary",
                        sleeper=_noop)
    if not good.passed:
        raise AssertionError("analysis must pass when metrics stay under bound")

    # A provider that breaches the threshold must fail, and must not run all
    # intervals (fail-fast).
    bad = analysis.run(lambda n, i: {"error_rate": 0.9}, "canary",
                       sleeper=_noop)
    if bad.passed:
        raise AssertionError("analysis must fail when the metric breaches")
    if bad.intervals_run != 1:
        raise AssertionError("analysis must abort on the first failing interval")


def check_explicit_rollback_and_history(pkg):
    """History tracks transitions and rollback_to / rollback_steps revert."""
    engine = FakeEngine()
    ctl = _controller(pkg, engine, "app", replicas=2)
    ctl.rollout(_rev(pkg, "app:1"))
    ctl.rollout(_rev(pkg, "app:2"))

    if getattr(ctl, "history", None) is None:
        raise AssertionError("controller must expose history()")
    hist = ctl.history()
    images = [e.image for e in hist.entries()]
    if "app:1" not in images or "app:2" not in images:
        raise AssertionError(
            "history must record both revisions, got {}".format(images)
        )
    if hist.current().image != "app:2":
        raise AssertionError("history current() must be the latest revision")

    # Explicit rollback to the previous revision.
    result = ctl.rollback_steps(steps=1)
    if result is None or not result.ok:
        raise AssertionError("rollback_steps(1) must succeed")
    if engine.images_live() != {"app:1"}:
        raise AssertionError(
            "rollback_steps(1) must return the service to app:1, got "
            "{}".format(engine.images_live())
        )


def check_scaling(pkg):
    """scale() changes replica count without changing the revision."""
    engine = FakeEngine()
    ctl = _controller(pkg, engine, "q", replicas=2)
    ctl.rollout(_rev(pkg, "q:1"))

    if getattr(ctl, "scale", None) is None:
        raise AssertionError("controller must expose scale()")

    up = ctl.scale(4)
    if not up.ok or engine.count() != 4:
        raise AssertionError("scale up must reach 4 replicas")
    if engine.images_live() != {"q:1"}:
        raise AssertionError("scaling must not change the running image")

    down = ctl.scale(1)
    if not down.ok or engine.count() != 1:
        raise AssertionError("scale down must reach 1 replica")

    noop = ctl.plan_scale(1)
    if not noop.is_noop:
        raise AssertionError("scaling to the current count must be a no-op")


def check_spec_loader(pkg):
    """RolloutSpec parses a manifest and cross-validates it."""
    if getattr(pkg, "RolloutSpec", None) is None:
        raise AssertionError("docker.rollout must expose RolloutSpec")
    RevisionError = getattr(pkg, "RevisionError", ValueError)

    spec = pkg.RolloutSpec.from_dict({
        "version": 1,
        "service": "web",
        "replicas": 4,
        "revision": {"image": "web:2", "environment": {"LOG": "info"}},
        "policy": {
            "strategy": "canary", "batch_size": 2, "canary_slots": 1,
            "canary": {"intervals": 2,
                       "metrics": [{"name": "error_rate", "threshold": 0.01}]},
        },
    })
    if spec.service != "web" or spec.revision.image != "web:2":
        raise AssertionError("spec must parse service and revision")
    if spec.policy.strategy != "canary" or not spec.policy.canary.checks:
        raise AssertionError("spec must parse the canary policy")

    # canary_slots > replicas is rejected.
    try:
        pkg.RolloutSpec.from_dict({
            "service": "x", "replicas": 2,
            "revision": {"image": "x:1"},
            "policy": {"strategy": "canary", "canary_slots": 5},
        })
    except Exception as exc:
        if not isinstance(exc, RevisionError):
            raise AssertionError("canary_slots > replicas must raise")
    else:
        raise AssertionError("canary_slots > replicas must raise")

    # a canary analysis block on a non-canary strategy is rejected.
    try:
        pkg.RolloutSpec.from_dict({
            "service": "x", "revision": {"image": "x:1"},
            "policy": {"strategy": "rolling",
                       "canary": {"metrics": [{"name": "e",
                                               "threshold": 1}]}},
        })
    except Exception as exc:
        if not isinstance(exc, RevisionError):
            raise AssertionError("canary block on rolling must raise")
    else:
        raise AssertionError("canary block on rolling must raise")


# ---------------------------------------------------------------------------

CHECKS = [
    check_revision_identity,
    check_policy_validation,
    check_initial_rollout,
    check_plan_is_pure_and_idempotent,
    check_rolling_batches,
    check_health_triggers_rollback,
    check_no_auto_rollback,
    check_blue_green_ordering,
    check_recreate,
    check_canary_success,
    check_canary_rejection_rolls_back,
    check_canary_metric_thresholds,
    check_explicit_rollback_and_history,
    check_scaling,
    check_spec_loader,
]


def main():
    _restore_tracked_sources()
    pkg = _load()

    for check in CHECKS:
        check(pkg)
        print("ok - {}".format(check.__name__))

    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
