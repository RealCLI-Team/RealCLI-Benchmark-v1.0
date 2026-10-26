#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Grader for the duplicate-reclaim-planner task.

The task asks the agent to add a NEW package, ``core.reclaim``, on top of
dupeGuru's pristine ``core.fs`` / ``core.directories`` primitives. This grader
verifies the *observable behaviour* of that package against a REAL temporary
filesystem -- it creates actual duplicate files, runs the planner/executor, and
checks what happened on disk. It never inspects the agent's implementation.

Anti-overfit design
--------------------
Before grading we run ``git checkout HEAD -- .`` inside /app. That restores every
*tracked* dupeGuru source file to its pristine state, so the agent cannot make
the tests pass by weakening the library it builds on. The agent's new
``core/reclaim/`` package is *untracked*, so it survives the restore untouched.
If the agent tried to satisfy the task by editing tracked files, those edits are
rolled back here and the behavioural checks then fail.

The grader is deliberately implementation-agnostic: it only uses names the task
tells the agent to export (``ReclaimSession``, ``ScanSpec``, strategies, action
kinds, the error classes, and the layered loader), and asserts end results.
"""

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

APP = "/app"

# dupeGuru's FilesDB is a module-level singleton that is only "connected" when
# the GUI app boots. Used as a library it stays unconnected and logs a warning
# for every digest it can't cache; that's expected and harmless (it just hashes
# live). Silence it so the grader output stays readable.
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
    import core.reclaim as R  # noqa: E402

    # Minimal surface sanity: the pieces the task requires must be importable.
    for attr in (
        "ReclaimSession",
        "ScanSpec",
        "ReclaimPlanner",
        "ReclaimExecutor",
        "group_identical",
        "make_strategy",
        "load_session",
        "DELETE",
        "TRASH",
        "HARDLINK",
        "SYMLINK",
    ):
        if not hasattr(R, attr):
            raise AssertionError(f"core.reclaim is missing required export: {attr}")
    return R


CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


class Tree:
    """A throwaway temp directory with helpers to build a file layout."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="reclaim_grade_"))

    def write(self, rel, data, mtime=None):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            data = data.encode()
        p.write_bytes(data)
        if mtime is not None:
            os.utime(p, (mtime, mtime))
        return p

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


# A blob helper: content that is well above dupeGuru's partial-hash threshold so
# the full staged comparison is meaningfully exercised.
def blob(tag, n=40000):
    return (tag.encode() if isinstance(tag, str) else tag) * n


# --------------------------------------------------------------------------- #
# Behavioural checks
# --------------------------------------------------------------------------- #
@check
def check_basic_grouping_and_reclaimable(R):
    t = Tree()
    try:
        A = blob("A")
        B = blob("B")
        t.write("docs/report.pdf", A, mtime=1000)
        t.write("docs/backup/report (1).pdf", A, mtime=2000)
        t.write("downloads/report copy.pdf", A, mtime=3000)
        t.write("pics/photo.jpg", B, mtime=5000)
        t.write("pics/dup/photo.jpg", B, mtime=6000)
        t.write("docs/unique.txt", blob("U"), mtime=1500)

        spec = R.ScanSpec(roots=[t.root])
        session = R.ReclaimSession(spec, strategy="oldest", action=R.DELETE)
        analysis = session.analyze()
        assert analysis.group_count == 2, f"expected 2 groups, got {analysis.group_count}"
        assert analysis.duplicate_count == 3, f"expected 3 dupes, got {analysis.duplicate_count}"
        expected = 2 * len(A) + 1 * len(B)
        assert analysis.reclaimable_bytes == expected, (
            f"reclaimable {analysis.reclaimable_bytes} != {expected}"
        )
    finally:
        t.cleanup()


@check
def check_plan_is_pure(R):
    """Analyzing/planning must not delete anything."""
    t = Tree()
    try:
        A = blob("P")
        t.write("a/x.dat", A, mtime=100)
        v = t.write("b/x.dat", A, mtime=200)
        spec = R.ScanSpec(roots=[t.root])
        session = R.ReclaimSession(spec, strategy="oldest")
        session.analyze()
        session.preview()  # dry-run
        assert v.exists(), "planning/preview must not remove files"
        assert (t.root / "a" / "x.dat").exists()
    finally:
        t.cleanup()


@check
def check_oldest_keeper(R):
    t = Tree()
    try:
        A = blob("O")
        t.write("new/f.dat", A, mtime=9000)
        old = t.write("old/f.dat", A, mtime=100)
        spec = R.ScanSpec(roots=[t.root])
        analysis = R.ReclaimSession(spec, strategy="oldest").analyze()
        keeper = analysis.plan.keepers[0]
        assert str(keeper.path) == str(old), f"oldest keeper wrong: {keeper.path}"
    finally:
        t.cleanup()


@check
def check_newest_keeper(R):
    t = Tree()
    try:
        A = blob("N")
        new = t.write("new/f.dat", A, mtime=9000)
        t.write("old/f.dat", A, mtime=100)
        spec = R.ScanSpec(roots=[t.root])
        analysis = R.ReclaimSession(spec, strategy="newest").analyze()
        assert str(analysis.plan.keepers[0].path) == str(new)
    finally:
        t.cleanup()


@check
def check_shortest_path_keeper(R):
    t = Tree()
    try:
        A = blob("S")
        shallow = t.write("f.dat", A, mtime=500)
        t.write("a/b/c/f.dat", A, mtime=100)
        spec = R.ScanSpec(roots=[t.root])
        analysis = R.ReclaimSession(spec, strategy="shortest_path").analyze()
        assert str(analysis.plan.keepers[0].path) == str(shallow)
    finally:
        t.cleanup()


@check
def check_cleanest_name_keeper(R):
    t = Tree()
    try:
        A = blob("C")
        clean = t.write("a/song.mp3", A, mtime=9000)   # newest, pristine name
        t.write("b/song (1).mp3", A, mtime=100)         # oldest, copy-like
        spec = R.ScanSpec(roots=[t.root])
        analysis = R.ReclaimSession(spec, strategy="cleanest_name").analyze()
        assert analysis.plan.keepers[0].path.name == "song.mp3", (
            f"cleanest_name picked {analysis.plan.keepers[0].path.name}"
        )
        _ = clean
    finally:
        t.cleanup()


@check
def check_composite_strategy(R):
    """Preferred-dir dominates, tie-break by clean name."""
    t = Tree()
    try:
        A = blob("K")
        pref = t.root / "originals"
        t.write("originals/x (1).dat", A, mtime=100)  # preferred but copy-like
        t.write("other/x.dat", A, mtime=200)           # clean but not preferred
        spec = R.ScanSpec(roots=[t.root])
        session = R.ReclaimSession(
            spec, strategy=[("preferred_dir", [str(pref)]), "cleanest_name"]
        )
        analysis = session.analyze()
        keeper = analysis.plan.keepers[0]
        assert str(keeper.path).startswith(str(pref)), (
            f"composite: preferred dir should win, got {keeper.path}"
        )
    finally:
        t.cleanup()


@check
def check_apply_deletes_and_keeps(R):
    t = Tree()
    try:
        A = blob("D")
        keep = t.write("keep/f.dat", A, mtime=100)
        v1 = t.write("d1/f.dat", A, mtime=200)
        v2 = t.write("d2/f.dat", A, mtime=300)
        spec = R.ScanSpec(roots=[t.root])
        session = R.ReclaimSession(spec, strategy="oldest", action=R.DELETE)
        report = session.apply()
        assert report.done_count == 2, f"expected 2 deletions, got {report.done_count}"
        assert not v1.exists() and not v2.exists(), "victims should be gone"
        assert keep.exists() and keep.read_bytes() == A, "keeper must survive intact"
    finally:
        t.cleanup()


@check
def check_dry_run_does_not_mutate(R):
    t = Tree()
    try:
        A = blob("Y")
        t.write("keep/f.dat", A, mtime=100)
        v = t.write("d/f.dat", A, mtime=200)
        spec = R.ScanSpec(roots=[t.root])
        session = R.ReclaimSession(spec, strategy="oldest")
        report = session.apply(dry_run=True)
        assert report.dry_run is True
        assert report.done_count == 1, "dry-run should still report the action count"
        assert v.exists(), "dry-run must not delete anything"
    finally:
        t.cleanup()


@check
def check_idempotency(R):
    t = Tree()
    try:
        A = blob("I")
        t.write("keep/f.dat", A, mtime=100)
        t.write("d/f.dat", A, mtime=200)
        spec = R.ScanSpec(roots=[t.root])
        R.ReclaimSession(spec, strategy="oldest").apply()
        # Fresh session, everything already collapsed.
        again = R.ReclaimSession(spec, strategy="oldest")
        analysis = again.analyze()
        assert analysis.group_count == 0, "no duplicates should remain"
        rep = again.apply()
        assert rep.done_count == 0, "second run must be a no-op"
    finally:
        t.cleanup()


@check
def check_reference_never_victim(R):
    """A file in a reference folder is protected: kept, never removed."""
    t = Tree()
    try:
        A = blob("R")
        masters = t.root / "masters"
        master = t.write("masters/orig.bin", A, mtime=100)
        stray = t.write("stray/copy.bin", A, mtime=200)
        spec = R.ScanSpec(roots=[t.root], reference_roots=[masters])
        # newest strategy would prefer the stray, but a protected file wins.
        session = R.ReclaimSession(spec, strategy="newest", action=R.DELETE)
        analysis = session.analyze()
        assert str(analysis.plan.keepers[0].path) == str(master), "master must be keeper"
        assert analysis.plan.victim_count == 1
        session.apply()
        assert master.exists(), "master must survive"
        assert not stray.exists(), "stray must be deleted"
    finally:
        t.cleanup()


@check
def check_hardlink_reclaims(R):
    t = Tree()
    try:
        A = blob("H")
        keep = t.write("keep.dat", A, mtime=100)
        v = t.write("sub/dupe.dat", A, mtime=200)
        spec = R.ScanSpec(roots=[t.root])
        session = R.ReclaimSession(spec, strategy="oldest", action=R.HARDLINK)
        session.apply()
        assert v.exists(), "hardlink leaves an accessible entry"
        assert v.read_bytes() == A, "linked content matches keeper"
        assert os.stat(keep).st_ino == os.stat(v).st_ino, "victim now shares keeper inode"
    finally:
        t.cleanup()


@check
def check_symlink_reclaims(R):
    t = Tree()
    try:
        A = blob("L")
        keep = t.write("keep.dat", A, mtime=100)
        v = t.write("sub/dupe.dat", A, mtime=200)
        spec = R.ScanSpec(roots=[t.root])
        session = R.ReclaimSession(spec, strategy="oldest", action=R.SYMLINK)
        session.apply()
        assert v.is_symlink(), "victim should become a symlink"
        assert Path(os.readlink(v)) == Path(keep), "symlink points to keeper"
    finally:
        t.cleanup()


@check
def check_trash_uses_injected_func(R):
    """The trash action must be delegated to an injectable callable (no real trashing)."""
    t = Tree()
    try:
        A = blob("T")
        t.write("keep.dat", A, mtime=100)
        v = t.write("dupe.dat", A, mtime=200)
        trashed = []
        spec = R.ScanSpec(roots=[t.root])
        session = R.ReclaimSession(spec, strategy="oldest", action=R.TRASH)
        rep = session.apply(trash_func=lambda p: trashed.append(str(p)))
        assert len(trashed) == 1, "trash_func should be called once"
        assert trashed[0] == str(v), "trash target must be the victim"
        assert rep.done_count == 1
    finally:
        t.cleanup()


@check
def check_staleness_guard(R):
    """A victim changed after planning must not be deleted blindly."""
    t = Tree()
    try:
        A = blob("Z")
        t.write("keep.dat", A, mtime=100)
        v = t.write("sub/dupe.dat", A, mtime=200)
        spec = R.ScanSpec(roots=[t.root])
        session = R.ReclaimSession(spec, strategy="oldest", action=R.DELETE)
        session.analyze()
        # Mutate the victim between plan and apply.
        v.write_bytes(A + b"CHANGED")
        os.utime(v, (999, 999))
        raised = False
        try:
            session.apply()
        except R.StalePlanError:
            raised = True
        assert raised, "apply must raise StalePlanError on a changed victim"
        assert v.exists(), "stale victim must not be deleted"
        # ignore_stale downgrades to a skip.
        rep = session.apply(ignore_stale=True)
        assert rep.skipped_count == 1 and rep.done_count == 0, "ignore_stale should skip"
        assert v.exists()
    finally:
        t.cleanup()


@check
def check_protected_victim_hard_error(R):
    """Feeding the executor a protected victim is a hard error (tamper guard)."""
    t = Tree()
    try:
        A = blob("Q")
        keeper_p = t.write("keep.dat", A, mtime=100)
        victim_p = t.write("dupe.dat", A, mtime=200)
        keeper = R.candidate_from_path(keeper_p)
        victim = R.candidate_from_path(victim_p, protected=True)
        action = R.ReclaimAction(R.DELETE, victim, keeper)
        plan = R.ReclaimPlan([action], [keeper], strategy_name="hand")
        executor = R.ReclaimExecutor()
        raised = False
        try:
            executor.execute(plan)
        except R.ProtectedPathError:
            raised = True
        assert raised, "executing an action on a protected file must raise"
        assert victim_p.exists(), "protected file must not be touched"
    finally:
        t.cleanup()


@check
def check_size_prefilter_no_false_group(R):
    """Same size but different content must not be grouped at full strength."""
    t = Tree()
    try:
        t.write("p.dat", b"A" * 40000, mtime=100)
        t.write("q.dat", b"B" * 40000, mtime=200)
        spec = R.ScanSpec(roots=[t.root])
        analysis = R.ReclaimSession(spec, strength=R.FULL).analyze()
        assert analysis.group_count == 0, "different content must not match"
    finally:
        t.cleanup()


@check
def check_size_only_strength(R):
    """SIZE_ONLY strength groups purely by size (a coarse mode users can pick)."""
    t = Tree()
    try:
        t.write("p.dat", b"A" * 40000, mtime=100)
        t.write("q.dat", b"B" * 40000, mtime=200)
        spec = R.ScanSpec(roots=[t.root])
        analysis = R.ReclaimSession(spec, strength=R.SIZE_ONLY).analyze()
        assert analysis.group_count == 1, "size-only should group equal-size files"
    finally:
        t.cleanup()


@check
def check_extension_filter(R):
    """include_ext restricts which files are even considered."""
    t = Tree()
    try:
        A = blob("E")
        t.write("a/pic.jpg", A, mtime=100)
        t.write("b/pic.jpg", A, mtime=200)
        # Same content but .mp4 -- excluded by include_ext, so not a dupe pair.
        t.write("a/clip.mp4", A, mtime=100)
        t.write("b/clip.mp4", A, mtime=200)
        spec = R.ScanSpec(roots=[t.root], include_ext=["jpg"])
        analysis = R.ReclaimSession(spec, strategy="oldest").analyze()
        assert analysis.group_count == 1, "only jpg files should be grouped"
        for k in analysis.plan.keepers:
            assert k.path.suffix == ".jpg"
    finally:
        t.cleanup()


@check
def check_excluded_root(R):
    """An excluded subfolder's files are not scanned."""
    t = Tree()
    try:
        A = blob("X")
        t.write("keep/f.dat", A, mtime=100)
        t.write("cache/f.dat", A, mtime=200)  # would be a dupe...
        spec = R.ScanSpec(roots=[t.root], excluded_roots=[t.root / "cache"])
        analysis = R.ReclaimSession(spec, strategy="oldest").analyze()
        # cache/f.dat excluded, so keep/f.dat is now unique -> no group.
        assert analysis.group_count == 0, "excluded folder must not contribute files"
    finally:
        t.cleanup()


@check
def check_zero_length_files_not_grouped_by_default(R):
    """Tiny/zero files are ignored by the default min_size (avoids noise)."""
    t = Tree()
    try:
        t.write("a/empty1", b"", mtime=100)
        t.write("b/empty2", b"", mtime=200)
        spec = R.ScanSpec(roots=[t.root])
        analysis = R.ReclaimSession(spec, strategy="oldest").analyze()
        assert analysis.group_count == 0, "empty files must be ignored by default"
    finally:
        t.cleanup()


@check
def check_min_size_override_groups_small(R):
    """Lowering min_size to 0 lets identical small files be grouped."""
    t = Tree()
    try:
        t.write("a/s.txt", b"hello world", mtime=100)
        t.write("b/s.txt", b"hello world", mtime=200)
        spec = R.ScanSpec(roots=[t.root])
        analysis = R.ReclaimSession(spec, strategy="oldest", min_size=0).analyze()
        assert analysis.group_count == 1, "min_size=0 should group identical small files"
    finally:
        t.cleanup()


@check
def check_unknown_strategy_rejected(R):
    raised = False
    try:
        R.make_strategy("does_not_exist")
    except R.UnknownStrategyError:
        raised = True
    assert raised, "unknown strategy name must raise UnknownStrategyError"


@check
def check_invalid_scan_root_rejected(R):
    raised = False
    try:
        R.CandidateScanner(R.ScanSpec(roots=["/definitely/not/here/xyz"])).scan()
    except R.InvalidScanRootError:
        raised = True
    assert raised, "a missing scan root must raise InvalidScanRootError"


@check
def check_layered_loader(R):
    """merge_documents/load_session: append lists, override scalars, null deletes."""
    base = {"roots": ["/data/a"], "strategy": "oldest", "include_ext": ["jpg"]}
    overlay = {"roots": ["/data/b"], "strategy": "newest", "include_ext": ["png"]}
    settings = R.load_settings([base, overlay])
    assert settings["roots"] == ["/data/a", "/data/b"], "roots should append across layers"
    assert settings["include_ext"] == ["jpg", "png"], "list fields append"
    assert settings["strategy"] == "newest", "scalars override"
    settings2 = R.load_settings([base, {"include_ext": None}])
    assert "include_ext" not in settings2, "null must delete an earlier key"


@check
def check_load_session_end_to_end(R):
    """A session built from layered config actually works on disk."""
    t = Tree()
    try:
        A = blob("W")
        keep = t.write("keep/f.dat", A, mtime=100)
        v = t.write("dupe/f.dat", A, mtime=200)
        base = {"roots": [str(t.root)], "strategy": "oldest", "action": R.DELETE}
        session = R.load_session([base])
        report = session.apply()
        assert report.done_count == 1
        assert keep.exists() and not v.exists()
    finally:
        t.cleanup()


@check
def check_fingerprint_content_hash_semantics(R):
    """identity_of: equal content -> equal identity; different -> different."""
    t = Tree()
    try:
        A = blob("F")
        p1 = t.write("a/x.dat", A, mtime=100)
        p2 = t.write("b/x.dat", A, mtime=200)
        p3 = t.write("c/y.dat", blob("G"), mtime=300)
        c1 = R.candidate_from_path(p1)
        c2 = R.candidate_from_path(p2)
        c3 = R.candidate_from_path(p3)
        assert c1.identity() == c2.identity(), "identical content -> equal identity"
        assert c1.identity() != c3.identity(), "different content -> unequal identity"
        assert R.same_content(c1.file, c2.file), "same_content true for identical"
        assert not R.same_content(c1.file, c3.file), "same_content false for different"
    finally:
        t.cleanup()


@check
def check_biggest_reclaim_ordered_first(R):
    """The plan lists the biggest-reclaim group's actions before smaller ones."""
    t = Tree()
    try:
        big = blob("BIG", 60000)
        small = blob("SML", 20000)
        t.write("big/a.dat", big, mtime=100)
        t.write("big/b.dat", big, mtime=200)
        t.write("small/a.dat", small, mtime=100)
        t.write("small/b.dat", small, mtime=200)
        spec = R.ScanSpec(roots=[t.root])
        analysis = R.ReclaimSession(spec, strategy="oldest").analyze()
        assert analysis.group_count == 2
        first_action = analysis.plan.actions[0]
        assert first_action.reclaimed_bytes == len(big), (
            "actions should be ordered biggest-reclaim-first"
        )
    finally:
        t.cleanup()


@check
def check_report_serialisable(R):
    """report.as_dict / render_text summarise without touching the filesystem."""
    t = Tree()
    try:
        A = blob("RP")
        t.write("keep/f.dat", A, mtime=100)
        v = t.write("dupe/f.dat", A, mtime=200)
        spec = R.ScanSpec(roots=[t.root])
        analysis = R.ReclaimSession(spec, strategy="oldest").analyze()
        d = R.report.as_dict(analysis)
        assert d["plan"]["victim_count"] == 1
        assert d["plan"]["reclaimable_bytes"] == len(A)
        text = R.report.render_text(analysis)
        assert isinstance(text, str) and "Reclaimable" in text
        assert v.exists(), "reporting must be pure"
    finally:
        t.cleanup()


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
def main():
    _restore_tracked_sources()
    try:
        R = _load()
    except Exception as ex:
        print("FATAL: could not import core.reclaim")
        traceback.print_exc()
        return 1

    passed = 0
    failed = 0
    for fn in CHECKS:
        name = fn.__name__
        try:
            fn(R)
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
