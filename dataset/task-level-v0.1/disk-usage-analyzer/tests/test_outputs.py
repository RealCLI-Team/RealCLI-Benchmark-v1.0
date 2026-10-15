#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Grader for the disk-usage-analyzer task.

The task asks the agent to add a NEW package, ``core.diskscan``, on top of
dupeGuru's pristine ``core.fs`` / ``core.directories`` primitives. This grader
verifies the *observable behaviour* of that package against a REAL temporary
filesystem -- it lays out actual files (with controlled sizes and mtimes),
runs the analyzer, and checks the numbers it reports. It never inspects the
agent's implementation.

Anti-overfit design
-------------------
Before grading we run ``git checkout HEAD -- .`` inside /app. That restores
every *tracked* dupeGuru source file to its pristine state, so the agent cannot
make the tests pass by weakening the library it builds on. The agent's new
``core/diskscan/`` package is *untracked*, so it survives the restore untouched.
If the agent tried to satisfy the task by editing tracked files, those edits are
rolled back here and the behavioural checks then fail.

The grader is deliberately implementation-agnostic: it only uses names the task
tells the agent to export, and asserts end results.
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
    import core.diskscan as D  # noqa: E402

    # Minimal surface sanity: the pieces the task requires must be importable.
    for attr in (
        "ScanSpec",
        "DiskScanSession",
        "FileEntry",
        "UsageTree",
        "AnalysisReport",
        "breakdown_by_extension",
        "breakdown_by_age",
        "CruftClassifier",
        "find_duplicate_sets",
        "load_session",
        "merge_documents",
        "allocated_size_of",
    ):
        if not hasattr(D, attr):
            raise AssertionError(f"core.diskscan is missing required export: {attr}")
    return D


CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


# A fixed clock so age-based checks are deterministic regardless of when the
# grader runs.
NOW = 1_700_000_000.0
DAY = 86400.0


class Tree:
    """A throwaway temp directory with helpers to build a file layout."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="diskscan_grade_"))

    def write(self, rel, data, age_days=None):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            data = data.encode()
        p.write_bytes(data)
        if age_days is not None:
            t = NOW - age_days * DAY
            os.utime(p, (t, t))
        return p

    def mkdir(self, rel):
        p = self.root / rel
        p.mkdir(parents=True, exist_ok=True)
        return p

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


# A blob helper: content well above dupeGuru's partial-hash threshold so the
# full staged comparison is meaningfully exercised.
def blob(tag, n=40000):
    return (tag.encode() if isinstance(tag, str) else tag) * n


def _session(D, tree, **kwargs):
    spec = kwargs.pop("spec", None)
    if spec is None:
        spec = D.ScanSpec(roots=[tree.root])
    return D.DiskScanSession(spec, clock=lambda: NOW, **kwargs)


# --------------------------------------------------------------------------- #
# Behavioural checks
# --------------------------------------------------------------------------- #
@check
def check_totals_and_file_count(D):
    t = Tree()
    try:
        t.write("a/x.bin", b"x" * 5000, age_days=10)
        t.write("a/y.bin", b"y" * 3000, age_days=10)
        t.write("b/z.bin", b"z" * 1000, age_days=10)
        report = _session(D, t).analyze()
        assert report.total_files == 3, f"expected 3 files, got {report.total_files}"
        assert report.total_apparent == 9000, f"apparent {report.total_apparent} != 9000"
        # allocated must be at least apparent (block rounding never shrinks a
        # non-sparse file) and a positive multiple of the block size.
        assert report.total_allocated >= report.total_apparent, "allocated < apparent"
    finally:
        t.cleanup()


@check
def check_allocated_uses_blocks_not_apparent(D):
    """A tiny file must report allocated size rounded up to a full block."""
    t = Tree()
    try:
        p = t.write("tiny.txt", b"hello", age_days=1)  # 5 bytes
        report = _session(D, t).analyze()
        entry = next(e for e in report.entries if e.path == p)
        assert entry.apparent_size == 5, f"apparent {entry.apparent_size} != 5"
        # On any real filesystem a 5-byte file occupies at least one 512-byte
        # stat block; in practice a 4 KiB fs block. Either way > apparent.
        st = p.stat()
        expected_alloc = D.allocated_size_of(st)
        assert entry.allocated_size == expected_alloc, (
            f"allocated {entry.allocated_size} != {expected_alloc}"
        )
        assert entry.allocated_size >= 512, "allocated below one stat block"
        assert entry.slack_bytes == entry.allocated_size - 5, "slack mismatch"
    finally:
        t.cleanup()


@check
def check_tree_rollup(D):
    """Parent subtree totals must equal the sum of their children + direct."""
    t = Tree()
    try:
        t.write("top/sub1/a.bin", b"a" * 4000, age_days=5)
        t.write("top/sub1/b.bin", b"b" * 2000, age_days=5)
        t.write("top/sub2/c.bin", b"c" * 6000, age_days=5)
        t.write("top/loose.bin", b"d" * 1000, age_days=5)
        report = _session(D, t).analyze()
        top = report.tree.node_at(t.root / "top")
        sub1 = report.tree.node_at(t.root / "top" / "sub1")
        sub2 = report.tree.node_at(t.root / "top" / "sub2")
        assert sub1.total_apparent == 6000, f"sub1 {sub1.total_apparent} != 6000"
        assert sub2.total_apparent == 6000, f"sub2 {sub2.total_apparent} != 6000"
        assert top.total_apparent == 13000, f"top {top.total_apparent} != 13000"
        assert top.total_files == 4, f"top files {top.total_files} != 4"
        # allocated rolls up the same way
        assert top.total_allocated == (
            sub1.total_allocated + sub2.total_allocated + top.direct_allocated
        ), "allocated roll-up inconsistent"
    finally:
        t.cleanup()


@check
def check_hotspots_ordered_biggest_first(D):
    t = Tree()
    try:
        t.write("small/a.bin", b"a" * 1000, age_days=5)
        t.write("medium/b.bin", b"b" * 50000, age_days=5)
        t.write("huge/c.bin", b"c" * 500000, age_days=5)
        report = _session(D, t).analyze()
        hs = report.hotspots(limit=3)
        allocs = [n.total_allocated for n in hs]
        assert allocs == sorted(allocs, reverse=True), f"hotspots not sorted: {allocs}"
        assert hs[0].path == t.root / "huge", f"biggest hotspot is {hs[0].path}"
    finally:
        t.cleanup()


@check
def check_largest_files(D):
    t = Tree()
    try:
        t.write("a.bin", b"a" * 1000, age_days=5)
        big = t.write("b.bin", b"b" * 90000, age_days=5)
        t.write("c.bin", b"c" * 5000, age_days=5)
        report = _session(D, t).analyze()
        largest = report.largest_files(limit=2)
        assert largest[0].path == big, f"largest file is {largest[0].path}"
        assert len(largest) == 2, "limit not honoured"
    finally:
        t.cleanup()


@check
def check_extension_breakdown(D):
    t = Tree()
    try:
        t.write("a.jpg", b"a" * 3000, age_days=5)
        t.write("b.jpg", b"b" * 3000, age_days=5)
        t.write("c.png", b"c" * 1000, age_days=5)
        t.write("noext", b"d" * 500, age_days=5)
        report = _session(D, t).analyze()
        ext = report.extension_breakdown
        jpg = ext.row_for("jpg")
        png = ext.row_for("png")
        assert jpg is not None and jpg.count == 2, f"jpg count {jpg.count if jpg else None}"
        assert jpg.apparent == 6000, f"jpg apparent {jpg.apparent}"
        assert png is not None and png.count == 1, "png count wrong"
        # rows come back heaviest-allocated first
        allocs = [r.allocated for r in ext]
        assert allocs == sorted(allocs, reverse=True), "extension rows not sorted"
    finally:
        t.cleanup()


@check
def check_age_breakdown(D):
    t = Tree()
    try:
        t.write("fresh.bin", b"f" * 1000, age_days=0.5)
        t.write("weekold.bin", b"w" * 1000, age_days=4)
        t.write("ancient.bin", b"z" * 1000, age_days=500)
        report = _session(D, t).analyze()
        age = report.age_breakdown
        # every entry lands in exactly one bucket, and totals add up
        assert age.total_count == 3, f"age total {age.total_count} != 3"
        older = age.row_for("older")
        assert older is not None and older.count == 1, "ancient not in 'older'"
        day = age.row_for("day")
        assert day is not None and day.count == 1, "fresh not in 'day'"
    finally:
        t.cleanup()


@check
def check_age_bucketing_is_total(D):
    """Every file must be counted in exactly one age bucket."""
    t = Tree()
    try:
        for i in range(12):
            t.write(f"f{i}.bin", b"x" * 500, age_days=i * 40)
        report = _session(D, t).analyze()
        age = report.age_breakdown
        assert age.total_count == 12, f"age buckets lost files: {age.total_count}"
        assert age.total_apparent == 12 * 500, "age apparent mismatch"
    finally:
        t.cleanup()


@check
def check_cruft_empty_file(D):
    t = Tree()
    try:
        t.write("real.bin", b"x" * 1000, age_days=5)
        t.write("empty.txt", b"", age_days=5)
        report = _session(D, t).analyze()
        names = {p.name for p in report.cruft.paths()}
        assert "empty.txt" in names, "empty file not flagged as cruft"
        assert "real.bin" not in names, "real file wrongly flagged"
    finally:
        t.cleanup()


@check
def check_cruft_categories(D):
    t = Tree()
    try:
        t.write("keep.dat", b"d" * 2000, age_days=5)
        t.write("junk.tmp", b"t" * 100, age_days=5)
        t.write("build/__pycache__/mod.pyc", b"c" * 200, age_days=5)
        t.write("logs/app.log", b"l" * 300, age_days=5)
        t.write("backup.bak", b"b" * 400, age_days=5)
        t.write("empty", b"", age_days=5)
        report = _session(D, t).analyze()
        cats = report.cruft.category_totals()
        assert D.CATEGORY_TEMP in cats, "no temp category"
        assert D.CATEGORY_CACHE in cats, "no cache category"
        assert D.CATEGORY_LOG in cats, "no log category"
        assert D.CATEGORY_BACKUP in cats, "no backup category"
        assert D.CATEGORY_EMPTY in cats, "no empty category"
        names = {p.name for p in report.cruft.paths()}
        assert "keep.dat" not in names, "real data flagged as cruft"
    finally:
        t.cleanup()


@check
def check_cruft_total_is_reclaimable_sum(D):
    t = Tree()
    try:
        t.write("keep.bin", b"k" * 5000, age_days=5)
        p1 = t.write("a.tmp", b"t" * 1000, age_days=5)
        p2 = t.write("b.tmp", b"t" * 2000, age_days=5)
        report = _session(D, t).analyze()
        tmp_tags = [tag for tag in report.cruft if tag.entry.path in (p1, p2)]
        expected = sum(tag.entry.allocated_size for tag in tmp_tags)
        got_from_totals = report.cruft.category_totals()[D.CATEGORY_TEMP][1]
        assert got_from_totals == expected, f"cruft temp alloc {got_from_totals} != {expected}"
    finally:
        t.cleanup()


@check
def check_reference_dir_exempt_from_cruft(D):
    """Files inside a reference (master) directory are never cruft."""
    t = Tree()
    try:
        t.write("scan/junk.tmp", b"t" * 100, age_days=5)
        t.write("masters/keep.tmp", b"m" * 100, age_days=5)
        spec = D.ScanSpec(
            roots=[t.root / "scan"],
            reference_roots=[t.root / "masters"],
        )
        report = _session(D, t, spec=spec).analyze()
        names = {p.name for p in report.cruft.paths()}
        assert "junk.tmp" in names, "scan-dir junk should be cruft"
        assert "keep.tmp" not in names, "reference-dir file must be exempt"
    finally:
        t.cleanup()


@check
def check_duplicate_waste(D):
    t = Tree()
    try:
        A = blob("DUP")
        t.write("a/one.bin", A, age_days=5)
        t.write("b/two.bin", A, age_days=5)
        t.write("c/three.bin", A, age_days=5)
        t.write("unique.bin", blob("UNIQ"), age_days=5)
        report = _session(D, t).analyze()
        dups = report.duplicates
        assert dups.group_count == 1, f"expected 1 dup group, got {dups.group_count}"
        assert dups.duplicate_count == 2, f"expected 2 redundant, got {dups.duplicate_count}"
        dset = dups.sets[0]
        assert dset.count == 3, f"dup set size {dset.count} != 3"
        assert dset.wasted_apparent == 2 * len(A), (
            f"wasted apparent {dset.wasted_apparent} != {2 * len(A)}"
        )
    finally:
        t.cleanup()


@check
def check_duplicate_size_prefilter_no_false_group(D):
    """Different-sized files must never be grouped as duplicates."""
    t = Tree()
    try:
        t.write("a.bin", blob("X", n=10000), age_days=5)
        t.write("b.bin", blob("X", n=10001), age_days=5)  # 1 char longer
        report = _session(D, t).analyze()
        assert report.duplicates.group_count == 0, "distinct sizes wrongly grouped"
    finally:
        t.cleanup()


@check
def check_duplicate_same_size_different_content(D):
    """Same size but different bytes must not be a duplicate."""
    t = Tree()
    try:
        t.write("a.bin", blob("A", n=20000), age_days=5)
        t.write("b.bin", blob("B", n=20000), age_days=5)  # same length, diff bytes
        report = _session(D, t).analyze()
        assert report.duplicates.group_count == 0, "different content wrongly grouped"
    finally:
        t.cleanup()


@check
def check_empty_files_not_duplicates(D):
    """Zero-byte files are cruft, but never counted as duplicate *data*."""
    t = Tree()
    try:
        t.write("a.empty", b"", age_days=5)
        t.write("b.empty", b"", age_days=5)
        report = _session(D, t).analyze()
        assert report.duplicates.group_count == 0, "empty files wrongly grouped as dupes"
    finally:
        t.cleanup()


@check
def check_empty_dir_detected(D):
    t = Tree()
    try:
        t.write("has/file.bin", b"x" * 1000, age_days=5)
        t.mkdir("empty_one")
        t.mkdir("empty_two/nested_empty")
        report = _session(D, t).analyze()
        empty_names = {n.name for n in report.tree.empty_dirs()}
        assert "empty_one" in empty_names, "empty_one not detected"
        assert "nested_empty" in empty_names, "nested empty dir not detected"
        assert "has" not in empty_names, "non-empty dir wrongly flagged"
    finally:
        t.cleanup()


@check
def check_excluded_root_not_scanned(D):
    t = Tree()
    try:
        t.write("keep/a.bin", b"a" * 1000, age_days=5)
        t.write("skip/b.bin", b"b" * 1000, age_days=5)
        spec = D.ScanSpec(roots=[t.root], excluded_roots=[t.root / "skip"])
        report = _session(D, t, spec=spec).analyze()
        names = {e.name for e in report.entries}
        assert "a.bin" in names, "kept file missing"
        assert "b.bin" not in names, "excluded file present"
    finally:
        t.cleanup()


@check
def check_extension_filter(D):
    t = Tree()
    try:
        t.write("photo.jpg", b"j" * 3000, age_days=5)
        t.write("movie.mp4", b"m" * 9000, age_days=5)
        spec = D.ScanSpec(roots=[t.root], include_ext=["jpg"])
        report = _session(D, t, spec=spec).analyze()
        names = {e.name for e in report.entries}
        assert "photo.jpg" in names, "included ext missing"
        assert "movie.mp4" not in names, "non-included ext present"
    finally:
        t.cleanup()


@check
def check_deterministic_order(D):
    """Two analyses of the same tree must produce identical entry ordering."""
    t = Tree()
    try:
        for i in range(20):
            t.write(f"d{i % 4}/f{i}.bin", bytes([i]) * (1000 + i), age_days=5)
        r1 = _session(D, t).analyze()
        r2 = _session(D, t).analyze(force=True)
        paths1 = [str(e.path) for e in r1.entries]
        paths2 = [str(e.path) for e in r2.entries]
        assert paths1 == paths2, "entry order not deterministic"
        assert paths1 == sorted(paths1), "entries not in sorted path order"
    finally:
        t.cleanup()


@check
def check_analyze_does_not_mutate_disk(D):
    """Analysis is read-only: nothing on disk changes."""
    t = Tree()
    try:
        t.write("a.tmp", b"junk", age_days=5)
        t.write("dup1.bin", blob("Z"), age_days=5)
        t.write("dup2.bin", blob("Z"), age_days=5)
        before = sorted(str(p) for p in t.root.rglob("*"))
        _session(D, t).analyze()
        after = sorted(str(p) for p in t.root.rglob("*"))
        assert before == after, "analysis changed the filesystem"
    finally:
        t.cleanup()


@check
def check_report_serialisable(D):
    t = Tree()
    try:
        t.write("a/x.jpg", blob("A"), age_days=5)
        t.write("b/y.jpg", blob("A"), age_days=5)
        t.write("junk.tmp", b"t" * 100, age_days=5)
        report = _session(D, t).analyze()
        d = report.as_dict()
        assert "totals" in d and "tree" in d, "as_dict missing keys"
        js = report.as_json()
        parsed = json.loads(js)
        assert parsed["totals"]["files"] == 3, "json totals wrong"
        txt = report.render_text()
        assert isinstance(txt, str) and len(txt) > 0, "render_text empty"
    finally:
        t.cleanup()


@check
def check_thresholds_pass_and_fail(D):
    t = Tree()
    try:
        t.write("a.empty", b"", age_days=5)
        t.write("b.empty", b"", age_days=5)
        t.write("dup1.bin", blob("Z"), age_days=5)
        t.write("dup2.bin", blob("Z"), age_days=5)
        session = _session(D, t)
        strict = D.default_thresholds(max_empty_files=0, max_duplicate_fraction=0.0)
        result = session.evaluate(strict)
        assert not result.passed, "strict thresholds should fail"
        names = {b.name for b in result}
        assert "empty-files" in names, "empty-files breach missing"
        assert "duplicate-fraction" in names, "duplicate-fraction breach missing"

        lenient = D.default_thresholds(max_total_allocated=10 ** 12)
        assert session.evaluate(lenient).passed, "lenient thresholds should pass"
    finally:
        t.cleanup()


@check
def check_threshold_severity_ordering(D):
    t = Tree()
    try:
        t.write("a.empty", b"", age_days=5)
        t.write("dup1.bin", blob("Z"), age_days=5)
        t.write("dup2.bin", blob("Z"), age_days=5)
        session = _session(D, t)
        ts = D.ThresholdSet([
            D.ThresholdRule(
                "info-rule",
                metric=lambda r: sum(1 for e in r.entries if e.is_empty),
                limit=0,
                severity=D.SEVERITY_INFO,
            ),
            D.ThresholdRule(
                "critical-rule",
                metric=lambda r: r.duplicates.wasted_allocated,
                limit=0,
                severity=D.SEVERITY_CRITICAL,
            ),
        ])
        result = session.evaluate(ts)
        assert result.worst_severity == D.SEVERITY_CRITICAL, "worst severity wrong"
        # most severe breach first
        assert result.breaches[0].severity == D.SEVERITY_CRITICAL, "not sorted by severity"
    finally:
        t.cleanup()


@check
def check_layered_loader(D):
    """Later docs override scalars; list fields append; null deletes."""
    base = {
        "roots": ["/tmp/base"],
        "include_ext": ["jpg"],
        "detect_duplicates": True,
    }
    overlay = {
        "roots": ["/tmp/extra"],
        "include_ext": ["png"],
        "detect_duplicates": None,  # delete
    }
    merged = D.merge_documents([base, overlay])
    assert set(merged["roots"]) == {"/tmp/base", "/tmp/extra"}, "roots not appended"
    assert set(merged["include_ext"]) == {"jpg", "png"}, "include_ext not appended"
    assert "detect_duplicates" not in merged, "null did not delete key"


@check
def check_load_session_end_to_end(D):
    t = Tree()
    try:
        t.write("photo.jpg", blob("A"), age_days=5)
        t.write("copy.jpg", blob("A"), age_days=5)
        t.write("skip.log", b"l" * 100, age_days=5)
        base = {"roots": [str(t.root)]}
        overlay = {"exclude_ext": ["log"]}
        session = D.load_session([base, overlay])
        report = session.analyze(ref_time=NOW)
        names = {e.name for e in report.entries}
        assert "photo.jpg" in names, "photo missing"
        assert "skip.log" not in names, "excluded log present"
        assert report.duplicates.group_count == 1, "dup not found through loader"
    finally:
        t.cleanup()


@check
def check_custom_age_ladder_via_config(D):
    t = Tree()
    try:
        t.write("recent.bin", b"r" * 1000, age_days=5)
        t.write("old.bin", b"o" * 1000, age_days=100)
        cfg = {
            "roots": [str(t.root)],
            "age_buckets": [["recent", DAY * 30], ["old", None]],
        }
        session = D.load_session([cfg])
        report = session.analyze(ref_time=NOW)
        labels = [r.key for r in report.age_breakdown]
        assert labels == ["recent", "old"], f"custom ladder labels {labels}"
        recent = report.age_breakdown.row_for("recent")
        old = report.age_breakdown.row_for("old")
        assert recent.count == 1 and old.count == 1, "custom bucket counts wrong"
    finally:
        t.cleanup()


@check
def check_thresholds_from_config(D):
    t = Tree()
    try:
        t.write("a.empty", b"", age_days=5)
        cfg = {
            "roots": [str(t.root)],
            "thresholds": {"max_empty_files": 0},
        }
        session = D.load_session([cfg])
        session.analyze(ref_time=NOW)
        thresholds = D.load_thresholds(D.load_settings([cfg]))
        assert thresholds is not None, "thresholds not built from config"
        result = session.evaluate(thresholds)
        names = {b.name for b in result}
        assert "empty-files" in names, "config threshold should fire on empty file"
    finally:
        t.cleanup()


@check
def check_invalid_scan_root_rejected(D):
    """A non-existent root is a config error, raised before any disk walk."""
    bogus = "/nonexistent/path/for/grading/12345"
    spec = D.ScanSpec(roots=[bogus])
    session = D.DiskScanSession(spec, clock=lambda: NOW)
    try:
        session.analyze()
    except D.InvalidScanRootError:
        return
    except D.DiskScanConfigError:
        return
    raise AssertionError("invalid scan root was not rejected")


@check
def check_sparse_file_allocated_below_apparent(D):
    """A sparse file reports allocated < apparent and zero slack."""
    t = Tree()
    try:
        p = t.root / "sparse.img"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as f:
            f.seek(10 * 1024 * 1024)  # 10 MiB hole
            f.write(b"end")
        os.utime(p, (NOW - 5 * DAY, NOW - 5 * DAY))
        report = _session(D, t).analyze()
        entry = next(e for e in report.entries if e.path == p)
        # Not every filesystem supports sparse files; only assert the invariant
        # when the OS actually made it sparse.
        if entry.allocated_size < entry.apparent_size:
            assert entry.is_sparse, "sparse not detected"
            assert entry.slack_bytes == 0, "sparse slack should clamp to 0"
    finally:
        t.cleanup()


@check
def check_reference_entries_counted_but_flagged(D):
    """Reference files still count toward usage totals but are marked."""
    t = Tree()
    try:
        t.write("scan/a.bin", b"a" * 4000, age_days=5)
        t.write("masters/b.bin", b"b" * 6000, age_days=5)
        spec = D.ScanSpec(
            roots=[t.root / "scan"],
            reference_roots=[t.root / "masters"],
        )
        report = _session(D, t, spec=spec).analyze()
        assert report.total_apparent == 10000, f"totals {report.total_apparent} != 10000"
        ref_entries = [e for e in report.entries if e.is_reference]
        assert len(ref_entries) == 1, "reference entry not flagged"
        assert ref_entries[0].name == "b.bin", "wrong reference entry"
    finally:
        t.cleanup()


@check
def check_top_directory_breakdown(D):
    t = Tree()
    try:
        t.write("photos/a.bin", b"a" * 5000, age_days=5)
        t.write("photos/sub/b.bin", b"b" * 5000, age_days=5)
        t.write("videos/c.bin", b"c" * 20000, age_days=5)
        report = _session(D, t).analyze()
        bd = D.breakdown_by_top_directory(report.tree, depth=1)
        videos = bd.row_for("videos")
        photos = bd.row_for("photos")
        assert videos is not None and videos.apparent == 20000, "videos total wrong"
        assert photos is not None and photos.apparent == 10000, "photos rollup wrong"
        # heaviest first
        assert bd.rows[0].key == "videos", "top-dir not sorted by size"
    finally:
        t.cleanup()


@check
def check_breakdown_top_folds_remainder(D):
    """Truncating a breakdown to top-N keeps the totals honest via '(other)'."""
    t = Tree()
    try:
        for i in range(6):
            t.write(f"f{i}.e{i}", bytes([i + 1]) * (1000 * (i + 1)), age_days=5)
        report = _session(D, t).analyze()
        full = report.extension_breakdown
        full_total = full.total_allocated
        topped = full.top(2)
        assert topped.total_allocated == full_total, "top() lost bytes"
        assert any(r.key == "(other)" for r in topped), "no (other) fold row"
    finally:
        t.cleanup()


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
def main():
    _restore_tracked_sources()
    try:
        D = _load()
    except Exception:
        print("FATAL: could not import core.diskscan")
        traceback.print_exc()
        return 1

    passed = 0
    failed = 0
    for fn in CHECKS:
        name = fn.__name__
        try:
            fn(D)
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
