#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Grader for the cloud-reclamation-planner task.

The task asks the agent to add a NEW package, ``libcloud.reclaim2``, on top of
Apache Libcloud's pristine resource model (``libcloud.compute.base`` /
``libcloud.storage.base`` / ``libcloud.compute.types``). Given an already-collected,
heterogeneous fleet of cloud resources, the package must distil them into uniform
"targets", recommend spend-reduction actions (stop idle nodes, destroy long-stopped
ones, delete unattached volumes, prune old / surplus snapshots, delete empty
containers, rightsize under-utilised nodes), project the monthly savings, and --
through *injected* action callables only -- preview (dry-run) or apply that plan
without ever touching a cloud API.

This grader verifies the *observable behaviour* of that package by constructing
real Libcloud resource objects in memory (``driver=None`` -- no network is ever
touched) drawn from several providers/regions/states, with an explicit ``as_of``
reference time so age maths is fully reproducible. It never inspects the agent's
implementation.

Anti-overfit design
-------------------
Before grading we run ``git checkout HEAD -- .`` inside /app. That restores every
*tracked* Libcloud source file to its pristine state, so the agent cannot make the
tests pass by weakening the library it builds on. The agent's new
``libcloud/reclaim2/`` package is *untracked*, so it survives the restore
untouched. If the agent tried to satisfy the task by editing tracked files, those
edits are rolled back here and the behavioural checks then fail.

The grader is deliberately implementation-agnostic: it only uses names the task
tells the agent to export, and asserts end results -- the right action for each
resource shape, protected resources never touched, retention keeping the newest N
per volume, deterministic biggest-saving-first plans, a 730-hour monthly
projection, dry-run vs apply with an explicit destructive gate and idempotency,
and a layered policy loader that merges predictably.
"""

import json
import logging
import subprocess
import sys
import traceback

APP = "/app"

logging.disable(logging.WARNING)

# Reproducible age anchor: epoch seconds. All "created" times are expressed as
# offsets from this so the grader never reads the wall clock.
AS_OF = 1_600_000_000.0
DAY = 86400.0


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #
def _restore_tracked_sources():
    try:
        subprocess.run(
            ["git", "-C", APP, "checkout", "HEAD", "--", "."],
            check=True,
            capture_output=True,
        )
    except Exception as ex:  # pragma: no cover
        print(f"WARNING: could not restore tracked sources: {ex}")


def _load():
    if APP not in sys.path:
        sys.path.insert(0, APP)
    import libcloud.reclaim2 as R  # noqa: E402

    required = (
        # errors
        "ReclaimError", "ReclaimConfigError", "PolicyError",
        "ProtectedResourceError",
        # money
        "Money", "monthly_from_hourly", "format_money", "HOURS_PER_MONTH",
        # target
        "ReclaimTarget", "build_target", "build_targets",
        # actions
        "Action", "Recommendation", "action_is_destructive", "savings_for",
        # rules
        "RuleConfig", "default_rules",
        # plan / planner
        "ReclaimPlan", "ReclaimPlanner", "RetentionConfig",
        # executor
        "Outcome", "ReclaimExecutor", "ExecutionReport",
        # session
        "ReclaimSession",
        # loader
        "merge_documents", "load_planner", "load_session",
    )
    missing = [name for name in required if not hasattr(R, name)]
    if missing:
        raise AssertionError("libcloud.reclaim2 missing exports: %s" % (missing,))
    return R


# --------------------------------------------------------------------------- #
# Fixtures: real Libcloud resource objects, no driver / no network.
# --------------------------------------------------------------------------- #
def _node(nid, state, hourly=0.1, idle=False, age_days=None, region="us-east-1",
          provider="aws", util=None, protected=False, extra=None):
    from libcloud.compute.base import Node, NodeSize
    size = NodeSize("m1.large", "m1.large", 8192, 40, 0, hourly, driver=None)
    e = {"provider": provider, "region": region}
    if idle:
        e["idle"] = True
    if age_days is not None:
        e["created_at"] = AS_OF - age_days * DAY
    if util is not None:
        e["utilisation"] = util
    if protected:
        e["protected"] = True
    if extra:
        e.update(extra)
    return Node(id=nid, name=nid, state=state, public_ips=[], private_ips=[],
                driver=None, size=size, extra=e)


def _volume(vid, gb, state="available", age_days=None, provider="aws",
            attached=None, hourly=0.02, region="us-east-1"):
    from libcloud.compute.base import StorageVolume
    e = {"provider": provider, "region": region, "hourly_cost": hourly}
    if age_days is not None:
        e["created_at"] = AS_OF - age_days * DAY
    if attached:
        e["attached_to"] = attached
    return StorageVolume(id=vid, name=vid, size=gb, driver=None, state=state, extra=e)


def _snapshot(sid, gb, age_days=None, volume_id=None, hourly=0.005, provider="aws"):
    from libcloud.compute.base import VolumeSnapshot
    e = {"provider": provider, "region": "us-east-1", "hourly_cost": hourly}
    if age_days is not None:
        e["created_at"] = AS_OF - age_days * DAY
    if volume_id is not None:
        e["volume_id"] = volume_id
    return VolumeSnapshot(id=sid, size=gb, driver=None, extra=e)


def _container(cid, total_bytes, age_days=None, provider="aws"):
    from libcloud.storage.base import Container
    e = {"provider": provider, "region": "us-east-1", "total_bytes": total_bytes,
         "hourly_cost": 0.001}
    if age_days is not None:
        e["created_at"] = AS_OF - age_days * DAY
    return Container(name=cid, extra=e, driver=None)


# --------------------------------------------------------------------------- #
# Check registry
# --------------------------------------------------------------------------- #
CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


def _acts_by_id(plan):
    return {rec.resource_id: rec.action for rec in plan}


# --------------------------------------------------------------------------- #
# Money / savings
# --------------------------------------------------------------------------- #
@check
def monthly_projection_uses_730_hours(R):
    assert R.HOURS_PER_MONTH == 730.0
    m = R.monthly_from_hourly(0.1)
    assert abs(m.amount - 73.0) < 1e-6, m.amount


@check
def money_is_currency_safe(R):
    a = R.Money(10.0, "USD")
    b = R.Money(5.0, "EUR")
    try:
        a + b
        raise AssertionError("expected currency mismatch to be refused")
    except ValueError:
        pass
    assert (R.Money(1.0, "USD") + R.Money(2.0, "USD")).amount == 3.0


@check
def format_money_renders_symbol(R):
    assert R.format_money(R.Money(1234.5, "USD")) == "$1,234.50"


# --------------------------------------------------------------------------- #
# Target distillation
# --------------------------------------------------------------------------- #
@check
def build_target_distils_cost_age_flags(R):
    t = R.build_target(
        _node("n1", "running", hourly=0.25, idle=True, age_days=10),
        as_of=AS_OF,
    )
    assert t.kind == "node", t.kind
    assert t.provider == "aws"
    assert abs(t.hourly_cost - 0.25) < 1e-9, t.hourly_cost
    assert abs(t.age_days - 10.0) < 1e-6, t.age_days
    assert t.idle is True
    assert t.protected is False


@check
def build_target_detects_protected(R):
    t = R.build_target(_node("p1", "stopped", age_days=100, protected=True), as_of=AS_OF)
    assert t.protected is True
    # protected via a tag key also works
    t2 = R.build_target(
        _node("p2", "stopped", age_days=100, extra={"tags": {"pinned": "true"}}),
        as_of=AS_OF,
    )
    assert t2.protected is True


@check
def build_targets_is_deterministic(R):
    fleet = [
        _node("z", "running", idle=True, age_days=5),
        _volume("a", 100, age_days=30),
        _snapshot("m", 10, age_days=200, volume_id="v9"),
    ]
    one = [t.identity for t in R.build_targets(fleet, as_of=AS_OF)]
    two = [t.identity for t in R.build_targets(list(reversed(fleet)), as_of=AS_OF)]
    assert one == two, (one, two)


@check
def ages_computed_against_as_of_not_wall_clock(R):
    # Two different as_of references yield two different ages for the same input.
    n = _node("clock", "running", idle=True, age_days=0)  # created == AS_OF
    t_early = R.build_target(n, as_of=AS_OF)
    t_late = R.build_target(n, as_of=AS_OF + 40 * DAY)
    assert abs(t_early.age_days - 0.0) < 1e-6, t_early.age_days
    assert abs(t_late.age_days - 40.0) < 1e-6, t_late.age_days


# --------------------------------------------------------------------------- #
# Individual rules -> actions
# --------------------------------------------------------------------------- #
@check
def idle_running_node_is_stopped(R):
    plan = R.ReclaimPlanner().plan(
        R.build_targets([_node("idle", "running", hourly=0.2, idle=True, age_days=10)], as_of=AS_OF)
    )
    assert _acts_by_id(plan).get("idle") == R.Action.STOP_NODE


@check
def busy_node_is_left_alone(R):
    # Running, not idle, decent utilisation -> nothing to do.
    plan = R.ReclaimPlanner().plan(
        R.build_targets([_node("busy", "running", hourly=0.2, idle=False, age_days=10, util=0.8)], as_of=AS_OF)
    )
    assert "busy" not in _acts_by_id(plan)


@check
def long_stopped_node_is_destroyed(R):
    plan = R.ReclaimPlanner().plan(
        R.build_targets([_node("old", "stopped", hourly=0.3, age_days=60)], as_of=AS_OF)
    )
    assert _acts_by_id(plan).get("old") == R.Action.DESTROY_NODE


@check
def recently_stopped_node_is_not_destroyed(R):
    plan = R.ReclaimPlanner().plan(
        R.build_targets([_node("fresh", "stopped", hourly=0.3, age_days=2)], as_of=AS_OF)
    )
    assert "fresh" not in _acts_by_id(plan)


@check
def unattached_aged_volume_is_deleted(R):
    plan = R.ReclaimPlanner().plan(
        R.build_targets([_volume("free", 100, state="available", age_days=30)], as_of=AS_OF)
    )
    assert _acts_by_id(plan).get("free") == R.Action.DELETE_VOLUME


@check
def attached_volume_is_left_alone(R):
    plan = R.ReclaimPlanner().plan(
        R.build_targets([_volume("used", 100, state="in-use", attached="i-123", age_days=30)], as_of=AS_OF)
    )
    assert "used" not in _acts_by_id(plan)


@check
def old_snapshot_is_pruned(R):
    plan = R.ReclaimPlanner().plan(
        R.build_targets([_snapshot("ancient", 10, age_days=200)], as_of=AS_OF)
    )
    assert _acts_by_id(plan).get("ancient") == R.Action.DELETE_SNAPSHOT


@check
def empty_aged_container_is_deleted(R):
    plan = R.ReclaimPlanner().plan(
        R.build_targets([_container("empty-bucket", 0, age_days=60)], as_of=AS_OF)
    )
    assert _acts_by_id(plan).get("empty-bucket") == R.Action.DELETE_CONTAINER


@check
def non_empty_container_is_left_alone(R):
    plan = R.ReclaimPlanner().plan(
        R.build_targets([_container("data", 5_000_000, age_days=60)], as_of=AS_OF)
    )
    assert "data" not in _acts_by_id(plan)


@check
def under_utilised_node_is_rightsized(R):
    plan = R.ReclaimPlanner().plan(
        R.build_targets([_node("under", "running", hourly=0.4, util=0.05)], as_of=AS_OF)
    )
    recs = {rec.resource_id: rec for rec in plan}
    assert "under" in recs
    assert recs["under"].action == R.Action.RESIZE_NODE
    # default fraction 0.5 of hourly, projected to 730h.
    assert abs(recs["under"].monthly_savings.amount - 0.2 * 730.0) < 1e-6, \
        recs["under"].monthly_savings.amount


# --------------------------------------------------------------------------- #
# Protected resources are never acted on
# --------------------------------------------------------------------------- #
@check
def protected_resource_is_never_recommended(R):
    fleet = R.build_targets([
        _node("keepme", "stopped", hourly=0.3, age_days=100, protected=True),
        _volume("keepvol", 500, state="available", age_days=100, provider="aws"),
    ], as_of=AS_OF)
    # mark the volume protected via override too, to be sure
    plan = R.ReclaimPlanner().plan(fleet)
    acts = _acts_by_id(plan)
    assert "keepme" not in acts, "protected node must not be recommended"
    assert any(t.resource_id == "keepme" for t in plan.skipped_protected)


# --------------------------------------------------------------------------- #
# Snapshot retention (cross-resource policy)
# --------------------------------------------------------------------------- #
@check
def retention_keeps_newest_n_per_volume(R):
    # 5 snapshots of v1, ages 1..5 days. keep_last=3 -> delete the 2 oldest.
    snaps = [_snapshot("s%d" % i, 10, age_days=i + 1, volume_id="v1") for i in range(5)]
    planner = R.ReclaimPlanner(retention=R.RetentionConfig(keep_last=3))
    plan = planner.plan(R.build_targets(snaps, as_of=AS_OF))
    deleted = sorted(rec.resource_id for rec in plan if rec.action == R.Action.DELETE_SNAPSHOT)
    # s0(1d) s1(2d) s2(3d) newest kept; s3(4d) s4(5d) deleted
    assert deleted == ["s3", "s4"], deleted


@check
def retention_leaves_small_groups_alone(R):
    snaps = [_snapshot("only%d" % i, 10, age_days=i + 1, volume_id="v2") for i in range(2)]
    planner = R.ReclaimPlanner(retention=R.RetentionConfig(keep_last=3))
    plan = planner.plan(R.build_targets(snaps, as_of=AS_OF))
    assert not [rec for rec in plan if rec.action == R.Action.DELETE_SNAPSHOT], list(plan)


# --------------------------------------------------------------------------- #
# Plan structure / rollups
# --------------------------------------------------------------------------- #
def _mixed_fleet(R):
    return R.build_targets([
        _node("idle", "running", hourly=0.2, idle=True, age_days=10),
        _node("stopped-old", "stopped", hourly=0.3, age_days=60),
        _volume("free", 100, state="available", age_days=30, hourly=0.05),
        _snapshot("ancient", 10, age_days=200),
        _container("empty-bucket", 0, age_days=60),
    ], as_of=AS_OF)


@check
def plan_has_one_action_per_resource(R):
    plan = R.ReclaimPlanner().plan(_mixed_fleet(R))
    ids = [rec.resource_id for rec in plan]
    assert len(ids) == len(set(ids)), ids


@check
def plan_is_sorted_biggest_saving_first(R):
    plan = R.ReclaimPlanner().plan(_mixed_fleet(R))
    amounts = [rec.monthly_savings.amount for rec in plan]
    assert amounts == sorted(amounts, reverse=True), amounts


@check
def plan_total_equals_sum_of_recommendations(R):
    plan = R.ReclaimPlanner().plan(_mixed_fleet(R))
    total = sum(rec.monthly_savings.amount for rec in plan)
    assert abs(plan.total_monthly_savings.amount - round(total, 2)) < 1e-6, \
        (plan.total_monthly_savings.amount, total)


@check
def plan_serialises_to_json(R):
    plan = R.ReclaimPlanner().plan(_mixed_fleet(R))
    blob = json.loads(plan.as_json())
    assert blob["count"] == len(list(plan))
    assert "total_monthly_savings" in blob
    assert "recommendations" in blob


@check
def plan_splits_destructive_and_reversible(R):
    plan = R.ReclaimPlanner().plan(_mixed_fleet(R))
    # stop-node is reversible; destroy/delete are destructive.
    assert plan.reversible, "expected at least one reversible recommendation"
    assert plan.destructive, "expected at least one destructive recommendation"
    for rec in plan.destructive:
        assert R.action_is_destructive(rec.action)


# --------------------------------------------------------------------------- #
# Executor: dry-run, apply, gate, protected guard, idempotency, failure
# --------------------------------------------------------------------------- #
@check
def dry_run_plans_everything_and_invokes_nothing(R):
    calls = []
    ex = R.ReclaimExecutor(actions={R.Action.STOP_NODE: lambda t, r: calls.append(t.resource_id)})
    plan = R.ReclaimPlanner().plan(_mixed_fleet(R))
    report = ex.execute(plan, dry_run=True)
    assert all(res.outcome == R.Outcome.PLANNED for res in report), report.outcome_counts()
    assert calls == [], calls
    assert report.realised_monthly_savings.amount == 0.0


@check
def apply_runs_injected_callables(R):
    seen = []

    def stop(t, r):
        seen.append(("stop", t.resource_id))
        return "stopped"

    ex = R.ReclaimExecutor(actions={R.Action.STOP_NODE: stop})
    plan = R.ReclaimPlanner().plan(
        R.build_targets([_node("idle", "running", idle=True, age_days=10)], as_of=AS_OF)
    )
    report = ex.execute(plan, dry_run=False)
    done = [res for res in report if res.outcome == R.Outcome.DONE]
    assert len(done) == 1, report.outcome_counts()
    assert ("stop", "idle") in seen


@check
def destructive_gate_skips_without_permission(R):
    def destroy(t, r):
        raise AssertionError("destroy must not be invoked without permission")

    ex = R.ReclaimExecutor(actions={R.Action.DESTROY_NODE: destroy})
    plan = R.ReclaimPlanner().plan(
        R.build_targets([_node("old", "stopped", hourly=0.3, age_days=60)], as_of=AS_OF)
    )
    report = ex.execute(plan, dry_run=False, allow_destructive=False)
    outcomes = {res.resource_id: res.outcome for res in report}
    assert outcomes.get("old") == R.Outcome.SKIPPED, outcomes


@check
def destructive_gate_runs_with_permission(R):
    ran = []
    ex = R.ReclaimExecutor(actions={R.Action.DESTROY_NODE: lambda t, r: ran.append(t.resource_id) or True})
    plan = R.ReclaimPlanner().plan(
        R.build_targets([_node("old", "stopped", hourly=0.3, age_days=60)], as_of=AS_OF)
    )
    report = ex.execute(plan, dry_run=False, allow_destructive=True)
    outcomes = {res.resource_id: res.outcome for res in report}
    assert outcomes.get("old") == R.Outcome.DONE, outcomes
    assert ran == ["old"]


@check
def protected_destructive_is_refused(R):
    # Construct a recommendation that routes a destructive action at a protected
    # target and hand it straight to the executor: it must refuse.
    t = R.build_target(_node("pp", "stopped", hourly=0.3, age_days=100, protected=True), as_of=AS_OF)
    rec = R.Recommendation(t, R.Action.DESTROY_NODE, "forced", R.monthly_from_hourly(0.3))

    class _OneRecPlan:
        currency = "USD"

        def __iter__(self):
            return iter([rec])

    ex = R.ReclaimExecutor(actions={R.Action.DESTROY_NODE: lambda t, r: True})
    try:
        ex.execute(_OneRecPlan(), dry_run=False, allow_destructive=True)
        raise AssertionError("expected ProtectedResourceError")
    except R.ProtectedResourceError:
        pass


@check
def noop_sentinel_is_idempotent(R):
    # A callable signalling "already gone" yields NOOP, not DONE.
    ex = R.ReclaimExecutor(actions={R.Action.STOP_NODE: lambda t, r: "already-gone"})
    plan = R.ReclaimPlanner().plan(
        R.build_targets([_node("idle", "running", idle=True, age_days=10)], as_of=AS_OF)
    )
    report = ex.execute(plan, dry_run=False)
    assert any(res.outcome == R.Outcome.NOOP for res in report), report.outcome_counts()
    assert report.realised_monthly_savings.amount == 0.0


@check
def failing_callable_is_captured_not_raised(R):
    def boom(t, r):
        raise RuntimeError("kaboom")

    ex = R.ReclaimExecutor(actions={R.Action.STOP_NODE: boom})
    plan = R.ReclaimPlanner().plan(
        R.build_targets([_node("idle", "running", idle=True, age_days=10)], as_of=AS_OF)
    )
    report = ex.execute(plan, dry_run=False)
    assert any(res.outcome == R.Outcome.FAILED for res in report), report.outcome_counts()
    assert report.ok is False


@check
def realised_vs_projected_savings_differ_when_some_skipped(R):
    # One reversible (stop) will be DONE; the destructive one SKIPPED.
    ex = R.ReclaimExecutor(actions={R.Action.STOP_NODE: lambda t, r: "stopped"})
    plan = R.ReclaimPlanner().plan(_mixed_fleet(R))
    report = ex.execute(plan, dry_run=False, allow_destructive=False)
    assert report.realised_monthly_savings.amount < report.projected_monthly_savings.amount, \
        (report.realised_monthly_savings.amount, report.projected_monthly_savings.amount)


# --------------------------------------------------------------------------- #
# Session end to end
# --------------------------------------------------------------------------- #
@check
def session_previews_then_applies(R):
    ex = R.ReclaimExecutor(actions={R.Action.STOP_NODE: lambda t, r: "stopped"})
    sess = R.ReclaimSession(as_of=AS_OF, executor=ex)
    sess.add_nodes([_node("idle", "running", idle=True, age_days=10)])
    sess.add_volumes([_volume("free", 100, state="available", age_days=30)])
    preview = sess.preview()
    assert preview.dry_run is True
    assert all(res.outcome == R.Outcome.PLANNED for res in preview)
    applied = sess.apply(allow_destructive=False)
    assert any(res.outcome == R.Outcome.DONE for res in applied)


@check
def session_cache_invalidates_on_add(R):
    sess = R.ReclaimSession(as_of=AS_OF)
    sess.add_nodes([_node("idle", "running", idle=True, age_days=10)])
    n1 = len(sess.targets())
    sess.add_nodes([_node("idle2", "running", idle=True, age_days=10)])
    n2 = len(sess.targets())
    assert n2 == n1 + 1, (n1, n2)


# --------------------------------------------------------------------------- #
# Layered config loader
# --------------------------------------------------------------------------- #
@check
def loader_merges_thresholds_and_builds_planner(R):
    planner = R.load_planner(
        {"thresholds": {"stopped_destroy_days": 10.0}},
        {"retention": {"keep_last": 1}},
    )
    assert isinstance(planner, R.ReclaimPlanner)
    # A node stopped 15 days is now destroyable under the tightened threshold.
    plan = planner.plan(R.build_targets([_node("s", "stopped", hourly=0.3, age_days=15)], as_of=AS_OF))
    assert _acts_by_id(plan).get("s") == R.Action.DESTROY_NODE


@check
def loader_none_deletes_key(R):
    merged = R.merge_documents({"currency": "USD"}, {"currency": None})
    assert "currency" not in merged, merged


@check
def loader_rejects_unknown_rule(R):
    try:
        R.load_planner({"enabled_rules": ["no-such-rule"]})
        raise AssertionError("expected PolicyError for unknown rule name")
    except R.PolicyError:
        pass


@check
def loader_rejects_non_dict_document(R):
    try:
        R.merge_documents([1, 2, 3])
        raise AssertionError("expected ReclaimConfigError for non-dict document")
    except R.ReclaimConfigError:
        pass


@check
def loader_can_narrow_to_a_rule_subset(R):
    # Only the idle-node rule enabled: a long-stopped node is no longer destroyed.
    sess = R.load_session({"enabled_rules": ["idle-node"]}, as_of=AS_OF)
    sess.add_nodes([
        _node("idle", "running", idle=True, age_days=10),
        _node("stopped-old", "stopped", hourly=0.3, age_days=60),
    ])
    acts = _acts_by_id(sess.plan())
    assert acts.get("idle") == R.Action.STOP_NODE
    assert "stopped-old" not in acts, acts


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
def main():
    _restore_tracked_sources()
    try:
        R = _load()
    except Exception as ex:
        print("FATAL: could not load libcloud.reclaim2: %s" % ex)
        traceback.print_exc()
        print("RESULT: FAILED")
        return 1

    passed = 0
    failed = 0
    for fn in CHECKS:
        try:
            fn(R)
        except Exception as ex:  # noqa: BLE001
            failed += 1
            print("FAIL %s: %s" % (fn.__name__, ex))
            traceback.print_exc()
        else:
            passed += 1
            print("PASS %s" % fn.__name__)

    total = passed + failed
    print("\n%d/%d checks passed" % (passed, total))
    if failed == 0:
        print("RESULT: ALL CHECKS PASSED")
        return 0
    print("RESULT: FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
