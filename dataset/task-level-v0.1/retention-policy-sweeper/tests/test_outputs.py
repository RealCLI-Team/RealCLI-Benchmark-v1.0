#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Grader for the retention-policy-sweeper task.

The task asks the agent to add a NEW package, ``core.sweep``, on top of
dupeGuru's pristine ``core.fs`` / ``core.directories`` primitives. This grader
verifies the *observable behaviour* of that package against a REAL temporary
filesystem -- it lays out actual files (with controlled sizes, mtimes and
atimes), asks the sweeper to plan and apply retention, and checks what it does.
It never inspects the agent's implementation.

Anti-overfit design
-------------------
Before grading we run ``git checkout HEAD -- .`` inside /app. That restores
every *tracked* dupeGuru source file to its pristine state, so the agent cannot
make the tests pass by weakening the library it builds on. The agent's new
``core/sweep/`` package is *untracked*, so it survives the restore untouched.
If the agent tried to satisfy the task by editing tracked files, those edits are
rolled back here and the behavioural checks then fail.

The grader is deliberately implementation-agnostic: it only uses names the task
tells the agent to export, and asserts end results. In particular it exercises
the safety contract that makes a sweeper trustworthy -- protected paths are
never evicted, a dry run mutates nothing, a stale plan is refused, and a plan is
idempotent -- on a real filesystem.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

APP = "/app"

# dupeGuru's FilesDB is a module-level singleton that is only "connected" when
# the GUI app boots. Used as a library it stays unconnected and logs a warning
# for every digest it can't cache; that's expected and harmless. Silence it so
# the grader output stays readable.
logging.disable(logging.WARNING)


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #
def _restore_tracked_sources():
    """Restore all tracked files under /app to their pristine committed state.

    This is what makes the grade honest: the agent's contribution is a new,
    untracked package, and everything the library ships with is reset here so it
    can't be quietly weakened to pass.
    """
    try:
        subprocess.run(
            ["git", "-C", APP, "checkout", "HEAD", "--", "."],
            check=True,
            capture_output=True,
        )
    except Exception as ex:  # pragma: no cover - only if git is misconfigured
        print(f"WARNING: could not restore tracked sources: {ex}")


def _load():
    if APP not in sys.path:
        sys.path.insert(0, APP)
    import core.sweep as S  # noqa: E402

    # Minimal surface sanity: the pieces the task requires must be importable.
    for attr in (
        "ScanSpec",
        "SweepSession",
        "SweepItem",
        "RetentionPolicy",
        "RuleSet",
        "make_rule",
        "make_ordering",
        "build_plan",
        "SweepExecutor",
        "load_session",
        "merge_documents",
        "ProtectedPathError",
        "StalePlanError",
    ):
        if not hasattr(S, attr):
            raise AssertionError(f"core.sweep is missing required export: {attr}")
    return S


CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


# A fixed clock so age/idle-based checks are deterministic regardless of when
# the grader runs.
NOW = 1_700_000_000.0
DAY = 86400.0


class Tree:
    """A throwaway temp directory with helpers to build a file layout."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="sweep_grade_"))

    def write(self, rel, data=b"x", age_days=None, idle_days=None):
        """Write a file. ``age_days`` sets mtime; ``idle_days`` sets atime.

        If ``idle_days`` is omitted it tracks ``age_days`` (a file untouched
        since last modified).
        """
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            data = data.encode()
        elif isinstance(data, int):
            data = b"a" * data
        p.write_bytes(data)
        mtime = NOW - (age_days * DAY if age_days is not None else 0)
        if idle_days is not None:
            atime = NOW - idle_days * DAY
        else:
            atime = mtime
        os.utime(p, (atime, mtime))
        return p

    def mkdir(self, rel):
        p = self.root / rel
        p.mkdir(parents=True, exist_ok=True)
        return p

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


def _session(S, tree, policy, references=None, excludes=None, clock=None, **spec_kw):
    spec = S.ScanSpec(
        roots=[tree.root],
        reference_roots=references or [],
        excluded_roots=excludes or [],
        **spec_kw,
    )
    return S.SweepSession(spec, policy, clock=clock or (lambda: NOW))


def _all_evict_policy(S, **kw):
    """A policy that votes to evict every (non-protected) item."""
    rs = S.RuleSet([S.make_rule({"kind": "all", "action": "evict"})])
    return S.RetentionPolicy(rule_set=rs, **kw)


# --------------------------------------------------------------------------- #
# Behavioural checks
# --------------------------------------------------------------------------- #
@check
def check_age_rule_selects_old_files(S):
    """An age rule evicts files older than its threshold, and only those."""
    t = Tree()
    try:
        t.write("a/fresh.log", b"x" * 100, age_days=1)
        t.write("a/stale1.log", b"x" * 100, age_days=40)
        t.write("a/stale2.log", b"x" * 100, age_days=90)
        pol = S.RetentionPolicy(
            rule_set=S.RuleSet([S.make_rule({"kind": "age", "action": "evict", "max_age_days": 30})]),
            action="trash",
        )
        plan = _session(S, t, pol).plan()
        names = sorted(a.item.name for a in plan.actions)
        assert names == ["stale1.log", "stale2.log"], f"age selection wrong: {names}"
    finally:
        t.cleanup()


@check
def check_dry_run_mutates_nothing(S):
    """preview() must perform no filesystem mutation."""
    t = Tree()
    try:
        paths = [t.write(f"t/f{i}.tmp", b"z" * 500, age_days=50) for i in range(4)]
        pol = S.RetentionPolicy(
            rule_set=S.RuleSet([S.make_rule({"kind": "glob", "action": "evict", "patterns": ["*.tmp"]})]),
            action="delete",
        )
        s = _session(S, t, pol)
        run = s.preview()
        assert run.dry_run is True, "preview should be a dry run"
        assert all(p.exists() for p in paths), "preview must not delete files"
        assert len(run.planned) == 4, f"expected 4 planned, got {len(run.planned)}"
        # Freed accounting on a dry run reports 0 actually freed.
        assert run.freed_size == 0, "dry run should free nothing"
    finally:
        t.cleanup()


@check
def check_apply_delete_removes_files(S):
    t = Tree()
    try:
        paths = [t.write(f"t/f{i}.tmp", 1000, age_days=50) for i in range(3)]
        pol = S.RetentionPolicy(
            rule_set=S.RuleSet([S.make_rule({"kind": "glob", "action": "evict", "patterns": ["*.tmp"]})]),
            action="delete",
        )
        s = _session(S, t, pol)
        s.preview()  # dry run first, must not remove
        assert all(p.exists() for p in paths), "preview removed files"
        report = s.apply()
        assert not any(p.exists() for p in paths), "apply did not remove files"
        assert len(report.acted) == 3, f"expected 3 acted, got {len(report.acted)}"
        assert report.freed_size == 3000, f"freed {report.freed_size} != 3000"
    finally:
        t.cleanup()


@check
def check_trash_uses_injected_func(S):
    """A trash action routes through the injected trash function, not delete."""
    t = Tree()
    try:
        p = t.write("t/a.tmp", 500, age_days=50)
        pol = S.RetentionPolicy(
            rule_set=S.RuleSet([S.make_rule({"kind": "glob", "action": "evict", "patterns": ["*.tmp"]})]),
            action="trash",
        )
        s = _session(S, t, pol)
        trashed = []
        report = s.apply(trash_func=lambda path: trashed.append(str(path)))
        assert trashed == [str(p)], f"trash_func not used: {trashed}"
        # The injected trash func didn't actually remove the file, but the run
        # should report it as trashed.
        assert len(report.of_outcome("trashed")) == 1, "trash outcome missing"
    finally:
        t.cleanup()


@check
def check_reference_files_never_evicted(S):
    """Files in a reference folder are protected from eviction."""
    t = Tree()
    try:
        t.write("data/old.log", b"x" * 100, age_days=100)
        t.write("golden/old.log", b"x" * 100, age_days=100)
        ref = t.root / "golden"
        pol = S.RetentionPolicy(
            rule_set=S.RuleSet([S.make_rule({"kind": "extension", "action": "evict", "extensions": ["log"]})]),
            action="trash",
        )
        s = _session(S, t, pol, references=[ref])
        plan = s.plan()
        paths = plan.paths
        assert len(plan.actions) == 1, f"reference not exempt: {len(plan.actions)}"
        assert str(t.root / "data" / "old.log") in paths, "wrong file evicted"
        assert not any("golden" in p for p in paths), "reference file was targeted"
    finally:
        t.cleanup()


@check
def check_excluded_root_not_scanned(S):
    t = Tree()
    try:
        t.write("keep/a.tmp", 100, age_days=50)
        t.write("skip/b.tmp", 100, age_days=50)
        pol = _all_evict_policy(S, action="delete")
        s = _session(S, t, pol, excludes=[t.root / "skip"])
        plan = s.plan()
        paths = plan.paths
        assert any("keep" in p for p in paths), "keep/ not scanned"
        assert not any("skip" in p for p in paths), "excluded dir was scanned"
    finally:
        t.cleanup()


@check
def check_keep_last_spares_best(S):
    """keep_last=N spares the N items the ordering would evict last."""
    t = Tree()
    try:
        # ages 0,10,20,30,40 days
        for i in range(5):
            t.write(f"log/app{i}.log", b"x" * 100, age_days=i * 10)
        pol = S.RetentionPolicy(
            rule_set=_evict_all_ruleset(S),
            ordering=S.make_ordering("oldest-first"),
            keep_last=2,
            action="trash",
        )
        plan = _session(S, t, pol).plan()
        # oldest-first evicts app4(40d),app3,app2,app1,app0; keep_last=2 spares
        # the two youngest (app0, app1).
        evicted = sorted(a.item.name for a in plan.actions)
        assert evicted == ["app2.log", "app3.log", "app4.log"], f"keep_last wrong: {evicted}"
    finally:
        t.cleanup()


def _evict_all_ruleset(S):
    return S.RuleSet([S.make_rule({"kind": "all", "action": "evict"})])


@check
def check_oldest_first_ordering(S):
    """Eviction order is oldest-first: the very first action is the oldest file."""
    t = Tree()
    try:
        for i in range(4):
            t.write(f"d/f{i}", b"x" * 100, age_days=i * 5)
        pol = S.RetentionPolicy(
            rule_set=_evict_all_ruleset(S), ordering=S.make_ordering("oldest-first"), action="trash"
        )
        plan = _session(S, t, pol).plan()
        # first action should be the oldest (f3, age 15d)
        assert plan.actions[0].item.name == "f3", f"first evicted {plan.actions[0].item.name}"
        assert plan.actions[-1].item.name == "f0", f"last evicted {plan.actions[-1].item.name}"
    finally:
        t.cleanup()


@check
def check_largest_first_ordering(S):
    t = Tree()
    try:
        t.write("d/small", 100, age_days=10)
        t.write("d/medium", 5000, age_days=10)
        t.write("d/large", 50000, age_days=10)
        pol = S.RetentionPolicy(
            rule_set=_evict_all_ruleset(S), ordering=S.make_ordering("largest-first"), keep_last=1, action="trash"
        )
        plan = _session(S, t, pol).plan()
        # largest-first: evict large, medium; keep_last=1 spares smallest.
        evicted = sorted(a.item.name for a in plan.actions)
        assert evicted == ["large", "medium"], f"largest-first wrong: {evicted}"
        assert plan.actions[0].item.name == "large", "largest not first"
    finally:
        t.cleanup()


@check
def check_lru_ordering_uses_atime(S):
    """The lru ordering evicts by idle time (atime), not modification age."""
    t = Tree()
    try:
        # All same mtime; differ only by atime (idle time).
        t.write("d/hot", b"x" * 100, age_days=1, idle_days=2)
        t.write("d/warm", b"x" * 100, age_days=1, idle_days=30)
        t.write("d/cold", b"x" * 100, age_days=1, idle_days=100)
        pol = S.RetentionPolicy(
            rule_set=_evict_all_ruleset(S), ordering=S.make_ordering("lru"), keep_last=1, action="trash"
        )
        plan = _session(S, t, pol).plan()
        # lru evicts most-idle first (cold, warm); keep_last=1 spares hot.
        evicted = sorted(a.item.name for a in plan.actions)
        assert evicted == ["cold", "warm"], f"lru wrong: {evicted}"
        assert plan.actions[0].item.name == "cold", "most-idle not first"
    finally:
        t.cleanup()


@check
def check_budget_drives_eviction(S):
    """A byte budget evicts (worst-first) until survivors fit under it."""
    t = Tree()
    try:
        # five 10k files; allocated will be >= apparent, but ordering by
        # allocated is monotone with our equal-size files -> use distinct sizes.
        sizes = {"a": 40000, "b": 30000, "c": 20000, "d": 10000, "e": 5000}
        for name, sz in sizes.items():
            t.write(f"c/{name}.bin", sz, age_days=10)
        pol = S.RetentionPolicy(
            rule_set=_evict_all_ruleset(S),
            ordering=S.make_ordering("largest-first"),
            max_total_bytes=40000,
            action="trash",
        )
        s = _session(S, t, pol)
        plan = s.plan()
        evict_paths = set(plan.paths)
        retained = [it for it in s.items if str(it.path) not in evict_paths]
        retained_bytes = sum(it.allocated_size for it in retained)
        assert retained_bytes <= 40000, f"budget not met: retained {retained_bytes}"
        # It should not over-evict: putting back the smallest evicted file would
        # exceed the budget. (We only assert the budget holds and something was
        # evicted, since allocated rounding can vary by filesystem.)
        assert len(plan.actions) >= 1, "budget evicted nothing despite overflow"
    finally:
        t.cleanup()


@check
def check_budget_respects_keep_last_then_overrides_when_needed(S):
    """keep_last spares items, but a tight budget can still reclaim them.

    With five equal-size files, keep_last=5 would spare all, but a budget of 0
    forces the sweeper to evict beyond keep_last to satisfy the hard limit.
    """
    t = Tree()
    try:
        for i in range(5):
            t.write(f"c/f{i}.bin", 10000, age_days=i)
        pol = S.RetentionPolicy(
            rule_set=_evict_all_ruleset(S),
            ordering=S.make_ordering("oldest-first"),
            keep_last=5,
            max_total_bytes=15000,
            action="trash",
        )
        s = _session(S, t, pol)
        plan = s.plan()
        evict_paths = set(plan.paths)
        retained_bytes = sum(it.allocated_size for it in s.items if str(it.path) not in evict_paths)
        assert retained_bytes <= 15000, f"budget overrides keep_last: retained {retained_bytes}"
        assert len(plan.actions) >= 1, "budget should force eviction past keep_last"
    finally:
        t.cleanup()


@check
def check_idempotent_reapply(S):
    """Applying the same plan twice converges: second run is 'already-gone'."""
    t = Tree()
    try:
        t.write("j/a.tmp", 400, age_days=10)
        pol = S.RetentionPolicy(
            rule_set=S.RuleSet([S.make_rule({"kind": "glob", "action": "evict", "patterns": ["*.tmp"]})]),
            action="delete",
        )
        s = _session(S, t, pol)
        plan = s.plan()
        ex = S.SweepExecutor(dry_run=False)
        r1 = ex.run(plan)
        r2 = ex.run(plan)
        assert len(r1.acted) == 1, f"first apply acted {len(r1.acted)}"
        assert len(r2.acted) == 0, "second apply acted on gone file"
        assert len(r2.already_gone) == 1, "second apply not idempotent"
    finally:
        t.cleanup()


@check
def check_stale_plan_refused(S):
    """A file that changed after planning makes apply raise StalePlanError."""
    t = Tree()
    try:
        p = t.write("s/a.tmp", 200, age_days=10)
        pol = S.RetentionPolicy(
            rule_set=S.RuleSet([S.make_rule({"kind": "glob", "action": "evict", "patterns": ["*.tmp"]})]),
            action="delete",
        )
        s = _session(S, t, pol)
        plan = s.plan()
        # Mutate the file after the plan was built.
        with open(p, "wb") as f:
            f.write(b"y" * 999)
        os.utime(p, (NOW, NOW))
        ex = S.SweepExecutor(dry_run=False)
        raised = False
        try:
            ex.run(plan)
        except S.StalePlanError:
            raised = True
        assert raised, "stale plan not refused"
        assert p.exists(), "stale file was removed despite refusal"
    finally:
        t.cleanup()


@check
def check_ignore_stale_skips(S):
    """With ignore_stale, a changed target is skipped, not fatal."""
    t = Tree()
    try:
        p = t.write("s/a.tmp", 200, age_days=10)
        other = t.write("s/b.tmp", 200, age_days=10)
        pol = S.RetentionPolicy(
            rule_set=S.RuleSet([S.make_rule({"kind": "glob", "action": "evict", "patterns": ["*.tmp"]})]),
            action="delete",
        )
        s = _session(S, t, pol)
        plan = s.plan()
        with open(p, "wb") as f:
            f.write(b"y" * 999)
        os.utime(p, (NOW, NOW))
        ex = S.SweepExecutor(dry_run=False, ignore_stale=True)
        r = ex.run(plan)
        assert len(r.skipped_stale) == 1, f"stale not skipped: {r.outcome_counts()}"
        assert p.exists(), "stale file removed under ignore_stale"
        assert not other.exists(), "non-stale sibling not removed"
    finally:
        t.cleanup()


@check
def check_protected_path_hard_error(S):
    """A plan that targets a protected path is refused before any mutation."""
    t = Tree()
    try:
        p1 = t.write("x/a.log", 100, age_days=10)
        p2 = t.write("x/b.log", 100, age_days=10)
        prot = S.SweepItem.from_path(p1, ref_time=NOW, protected=True)
        normal = S.SweepItem.from_path(p2, ref_time=NOW, protected=False)
        # Hand-build a (malformed) plan that includes a protected target.
        from core.sweep.plan import SweepAction, SweepPlan  # public submodule

        bad_plan = SweepPlan([SweepAction(normal, "delete"), SweepAction(prot, "delete")])
        ex = S.SweepExecutor(dry_run=False)
        raised = False
        try:
            ex.run(bad_plan)
        except S.ProtectedPathError:
            raised = True
        assert raised, "protected target not rejected"
        # Whole run refused up-front: nothing removed.
        assert p1.exists() and p2.exists(), "files removed despite protected refusal"
    finally:
        t.cleanup()


@check
def check_policy_select_excludes_protected(S):
    """A well-formed plan never contains a protected item, even if a rule matches."""
    t = Tree()
    try:
        t.write("data/a.log", 100, age_days=100)
        t.write("ref/b.log", 100, age_days=100)
        pol = S.RetentionPolicy(rule_set=_evict_all_ruleset(S), action="delete")
        s = _session(S, t, pol, references=[t.root / "ref"])
        plan = s.plan()
        assert all("ref" not in p for p in plan.paths), "protected item in plan"
    finally:
        t.cleanup()


@check
def check_rule_precedence_keep_beats_evict(S):
    """At equal priority a keep vote beats an evict vote."""
    t = Tree()
    try:
        p = t.write("p/a.tmp", 100, age_days=100)
        item = S.SweepItem.from_path(p, ref_time=NOW)
        rs = S.RuleSet([
            S.make_rule({"kind": "glob", "name": "e", "action": "evict", "patterns": ["*.tmp"], "priority": 1}),
            S.make_rule({"kind": "glob", "name": "k", "action": "keep", "patterns": ["*.tmp"], "priority": 1}),
        ])
        v = rs.classify(item)
        assert v.action == "keep", f"keep should win tie, got {v.action}"
    finally:
        t.cleanup()


@check
def check_rule_precedence_priority_wins(S):
    """A higher-priority rule overrides a lower one of the opposite vote."""
    t = Tree()
    try:
        p = t.write("p/a.tmp", 100, age_days=100)
        item = S.SweepItem.from_path(p, ref_time=NOW)
        rs = S.RuleSet([
            S.make_rule({"kind": "glob", "name": "e", "action": "evict", "patterns": ["*.tmp"], "priority": 9}),
            S.make_rule({"kind": "glob", "name": "k", "action": "keep", "patterns": ["*.tmp"], "priority": 1}),
        ])
        v = rs.classify(item)
        assert v.action == "evict", f"higher-priority evict should win, got {v.action}"
    finally:
        t.cleanup()


@check
def check_default_action_keep(S):
    """An item no rule matches falls to the default action (keep)."""
    t = Tree()
    try:
        t.write("d/a.dat", 100, age_days=100)
        rs = S.RuleSet([S.make_rule({"kind": "glob", "action": "evict", "patterns": ["*.tmp"]})])
        pol = S.RetentionPolicy(rule_set=rs, action="delete")
        plan = _session(S, t, pol).plan()
        assert plan.action_count == 0, f"unmatched file should be kept, got {plan.action_count}"
    finally:
        t.cleanup()


@check
def check_size_rule(S):
    """A size rule matches by apparent size within its bounds."""
    t = Tree()
    try:
        t.write("d/small.bin", 100, age_days=10)
        t.write("d/big.bin", 100000, age_days=10)
        rs = S.RuleSet([S.make_rule({"kind": "size", "action": "evict", "min_size": 50000})])
        pol = S.RetentionPolicy(rule_set=rs, action="delete")
        plan = _session(S, t, pol).plan()
        names = [a.item.name for a in plan.actions]
        assert names == ["big.bin"], f"size rule selection wrong: {names}"
    finally:
        t.cleanup()


@check
def check_idle_rule(S):
    """An idle rule matches by time since last access (atime)."""
    t = Tree()
    try:
        # Same age, different idle time.
        t.write("d/active", b"x" * 100, age_days=100, idle_days=1)
        t.write("d/dormant", b"x" * 100, age_days=100, idle_days=90)
        rs = S.RuleSet([S.make_rule({"kind": "idle", "action": "evict", "max_idle_days": 30})])
        pol = S.RetentionPolicy(rule_set=rs, action="delete")
        plan = _session(S, t, pol).plan()
        names = [a.item.name for a in plan.actions]
        assert names == ["dormant"], f"idle rule selection wrong: {names}"
    finally:
        t.cleanup()


@check
def check_extension_and_glob_rules(S):
    t = Tree()
    try:
        t.write("d/a.log", 100, age_days=10)
        t.write("d/b.txt", 100, age_days=10)
        t.write("d/keepme.bak", 100, age_days=10)
        rs = S.RuleSet([
            S.make_rule({"kind": "extension", "action": "evict", "extensions": ["log"]}),
            S.make_rule({"kind": "glob", "action": "evict", "patterns": ["*.bak"]}),
        ])
        pol = S.RetentionPolicy(rule_set=rs, action="delete")
        plan = _session(S, t, pol).plan()
        names = sorted(a.item.name for a in plan.actions)
        assert names == ["a.log", "keepme.bak"], f"ext/glob selection wrong: {names}"
    finally:
        t.cleanup()


@check
def check_deterministic_order(S):
    """Two plans over the same layout produce identical action ordering."""
    t = Tree()
    try:
        for i in range(6):
            t.write(f"d/sub{i % 3}/f{i}", b"x" * (100 + i), age_days=i)
        pol = S.RetentionPolicy(rule_set=_evict_all_ruleset(S), ordering=S.make_ordering("oldest-first"), action="trash")
        p1 = _session(S, t, pol).plan().paths
        p2 = _session(S, t, pol).plan().paths
        assert p1 == p2, "plan ordering not deterministic"
    finally:
        t.cleanup()


@check
def check_plan_is_pure(S):
    """Building a plan must not touch the filesystem."""
    t = Tree()
    try:
        paths = [t.write(f"d/f{i}.tmp", 100, age_days=50) for i in range(3)]
        pol = S.RetentionPolicy(
            rule_set=S.RuleSet([S.make_rule({"kind": "glob", "action": "evict", "patterns": ["*.tmp"]})]),
            action="delete",
        )
        s = _session(S, t, pol)
        plan = s.plan()
        _ = plan.as_dict()
        assert all(p.exists() for p in paths), "planning removed files"
        assert plan.action_count == 3, "plan wrong size"
    finally:
        t.cleanup()


@check
def check_plan_serialisable(S):
    t = Tree()
    try:
        t.write("d/a.tmp", 2048, age_days=50)
        pol = S.RetentionPolicy(
            rule_set=S.RuleSet([S.make_rule({"kind": "glob", "action": "evict", "patterns": ["*.tmp"]})]),
            action="trash",
        )
        plan = _session(S, t, pol).plan()
        d = plan.as_dict()
        js = json.dumps(d)  # must be JSON-serialisable
        back = json.loads(js)
        assert back["action_count"] == 1, "serialised action_count wrong"
        assert back["reclaimable_size"] == 2048, f"serialised size {back['reclaimable_size']}"
        assert back["actions"][0]["verb"] == "trash", "serialised verb wrong"
    finally:
        t.cleanup()


@check
def check_reclaimable_accounting(S):
    """Plan reports the sum of targeted apparent sizes as reclaimable."""
    t = Tree()
    try:
        t.write("d/a.tmp", 1000, age_days=50)
        t.write("d/b.tmp", 2500, age_days=50)
        t.write("d/keep.dat", 9999, age_days=50)
        pol = S.RetentionPolicy(
            rule_set=S.RuleSet([S.make_rule({"kind": "glob", "action": "evict", "patterns": ["*.tmp"]})]),
            action="delete",
        )
        plan = _session(S, t, pol).plan()
        assert plan.reclaimable_size == 3500, f"reclaimable {plan.reclaimable_size} != 3500"
        assert plan.reclaimable_allocated >= 3500, "allocated reclaim < apparent"
    finally:
        t.cleanup()


@check
def check_layered_loader_merge_and_delete(S):
    """merge_documents deep-merges: scalars override, lists append, null deletes."""
    base = {
        "scan": {"roots": ["/a"], "include_ext": ["log"]},
        "policy": {"action": "trash", "keep_last": 5, "rules": [{"kind": "age", "action": "evict", "max_age_days": 30}]},
    }
    overlay = {
        "scan": {"roots": ["/b"], "include_ext": ["tmp"]},
        "policy": {"keep_last": 2, "rules": [{"kind": "glob", "action": "evict", "patterns": ["*.bak"]}]},
    }
    merged = S.merge_documents(base, overlay)
    assert set(merged["scan"]["roots"]) == {"/a", "/b"}, f"roots not appended: {merged['scan']['roots']}"
    assert set(merged["scan"]["include_ext"]) == {"log", "tmp"}, "include_ext not appended"
    assert merged["policy"]["keep_last"] == 2, "scalar not overridden"
    assert len(merged["policy"]["rules"]) == 2, "rules not appended"
    # null deletes a key
    merged2 = S.merge_documents(base, {"policy": {"keep_last": None}})
    assert "keep_last" not in merged2["policy"], "null did not delete key"


@check
def check_load_session_end_to_end(S):
    """load_session builds a working session from layered config documents."""
    t = Tree()
    try:
        t.write("a/old.log", b"x" * 100, age_days=100)
        t.write("a/new.log", b"x" * 100, age_days=1)
        base = {
            "scan": {"roots": [str(t.root)]},
            "policy": {"action": "trash", "rules": [{"kind": "age", "action": "evict", "max_age_days": 30}]},
        }
        overlay = {"policy": {"rules": [{"kind": "glob", "name": "pin", "action": "keep",
                                          "priority": 10, "patterns": ["*.pinned"]}]}}
        s = S.load_session(base, overlay, clock=lambda: NOW)
        plan = s.plan()
        names = [a.item.name for a in plan.actions]
        assert names == ["old.log"], f"loader end-to-end wrong: {names}"
    finally:
        t.cleanup()


@check
def check_unknown_rule_kind_rejected(S):
    """An unknown rule kind is a configuration error, raised before any I/O."""
    try:
        S.make_rule({"kind": "definitely-not-a-rule", "action": "evict"})
    except Exception as ex:
        assert "sweep" in type(ex).__module__ or type(ex).__name__.endswith("Error"), (
            f"unexpected error type {type(ex).__name__}"
        )
        return
    raise AssertionError("unknown rule kind did not raise")


@check
def check_unknown_ordering_rejected(S):
    try:
        S.make_ordering("definitely-not-an-ordering")
    except Exception as ex:
        assert type(ex).__name__.endswith("Error"), f"unexpected error {type(ex).__name__}"
        return
    raise AssertionError("unknown ordering did not raise")


@check
def check_invalid_scan_root_rejected(S):
    """Scanning a nonexistent root is a config error, raised before mutation."""
    missing = Path(tempfile.gettempdir()) / "sweep_grade_does_not_exist_zzz"
    if missing.exists():  # pragma: no cover
        shutil.rmtree(missing, ignore_errors=True)
    spec = S.ScanSpec(roots=[missing])
    pol = _all_evict_policy(S, action="trash")
    s = S.SweepSession(spec, pol, clock=lambda: NOW)
    try:
        s.scan()
    except Exception as ex:
        assert type(ex).__name__.endswith("Error"), f"unexpected error {type(ex).__name__}"
        return
    raise AssertionError("invalid scan root did not raise")


@check
def check_vanished_file_not_stale(S):
    """A file that vanished after planning is 'already-gone', not stale-fatal."""
    t = Tree()
    try:
        p = t.write("v/a.tmp", 300, age_days=10)
        pol = S.RetentionPolicy(
            rule_set=S.RuleSet([S.make_rule({"kind": "glob", "action": "evict", "patterns": ["*.tmp"]})]),
            action="delete",
        )
        s = _session(S, t, pol)
        plan = s.plan()
        p.unlink()  # remove out from under the plan
        ex = S.SweepExecutor(dry_run=False)
        r = ex.run(plan)  # must NOT raise
        assert len(r.already_gone) == 1, f"vanished not handled as already-gone: {r.outcome_counts()}"
    finally:
        t.cleanup()


@check
def check_size_filter_on_scan(S):
    """ScanSpec min_size/max_size filters which files even enter the run."""
    t = Tree()
    try:
        t.write("d/tiny.bin", 10, age_days=10)
        t.write("d/big.bin", 100000, age_days=10)
        pol = _all_evict_policy(S, action="trash")
        s = _session(S, t, pol, min_size=1000)
        plan = s.plan()
        names = [a.item.name for a in plan.actions]
        assert names == ["big.bin"], f"scan size filter wrong: {names}"
    finally:
        t.cleanup()


@check
def check_report_renders(S):
    """SweepReport turns a plan (+run) into dict / json / text."""
    t = Tree()
    try:
        t.write("d/a.tmp", 4096, age_days=50)
        pol = S.RetentionPolicy(
            rule_set=S.RuleSet([S.make_rule({"kind": "glob", "action": "evict", "patterns": ["*.tmp"]})]),
            action="trash",
        )
        s = _session(S, t, pol)
        plan = s.plan()
        run = s.preview()
        report = S.SweepReport(plan, run=run, policy=pol)
        d = report.as_dict()
        assert d["action_count"] == 1, "report dict wrong"
        js = report.as_json()
        assert json.loads(js)["action_count"] == 1, "report json wrong"
        txt = report.render_text()
        assert isinstance(txt, str) and len(txt) > 0, "report text empty"
    finally:
        t.cleanup()


@check
def check_apply_reuses_previewed_plan(S):
    """preview() then apply() act on the same snapshot: what you saw runs."""
    t = Tree()
    try:
        paths = [t.write(f"d/f{i}.tmp", 500, age_days=50) for i in range(3)]
        pol = S.RetentionPolicy(
            rule_set=S.RuleSet([S.make_rule({"kind": "glob", "action": "evict", "patterns": ["*.tmp"]})]),
            action="delete",
        )
        s = _session(S, t, pol)
        preview = s.preview()
        previewed_paths = sorted(o.action.item.path for o in preview.planned)
        report = s.apply()
        applied_paths = sorted(o.action.item.path for o in report.acted)
        assert previewed_paths == applied_paths, "apply diverged from preview"
    finally:
        t.cleanup()


# --------------------------------------------------------------------------- #
def main():
    _restore_tracked_sources()
    try:
        S = _load()
    except Exception:
        print("FATAL: could not import core.sweep")
        traceback.print_exc()
        return 1

    passed = 0
    failed = 0
    for fn in CHECKS:
        name = fn.__name__
        try:
            fn(S)
            print(f"  ok  {name}")
            passed += 1
        except Exception as ex:
            failed += 1
            print(f"FAIL  {name}: {ex}")
            traceback.print_exc()

    total = passed + failed
    print(f"\n{passed}/{total} checks passed")
    if failed:
        print("RESULT: FAIL")
        return 1
    print("RESULT: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
