#!/usr/bin/env python3
"""Grade the copy-on-write overlay filesystem the agent was asked to build.

Two independent things are checked:

1.  **Conformance.** With an *empty* read-only lower layer, the overlay
    must behave exactly like an ordinary writable filesystem, so we run
    the project's own standard suite (``fs.test.FSTestCases``) against it,
    both with an implicit and an explicit upper layer.  This is
    implementation-agnostic: any backend that honours the PyFilesystem2
    contract passes; a subtly-wrong one fails.

2.  **Copy-on-write semantics.** Over a *populated* lower layer the
    overlay must (a) serve reads by falling through to the lower layer,
    (b) never mutate the lower layer on write, delete or metadata change,
    (c) hide deleted lower resources from the merged view, and (d) keep a
    recreated directory from resurrecting stale lower children.  These are
    all exercised through the public ``FS`` interface only -- the grade
    never inspects how whiteouts or copy-up are represented internally, so
    any correct implementation strategy is accepted.

To keep the grade honest the tracked source tree is restored from git
before anything is imported, so the conformance suite and the base ``FS``
class are always the pristine upstream versions -- only the agent's new,
untracked backend module is exercised.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

APP = "/app"


def _restore_tracked_sources() -> None:
    """Revert any edits to git-tracked files under /app.

    The agent is asked to *add* a new backend, not to modify the library.
    Restoring tracked files guarantees the conformance suite and the base
    ``FS`` class are the real upstream code, so the backend cannot be
    "passed" by weakening the tests it is graded against.
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


def _load_backend():
    """Import the agent's overlay backend and return its ``FS`` subclass.

    The backend is expected to live at ``fs/overlayfs.py`` and to expose a
    class named ``OverlayFS`` constructed as ``OverlayFS(lower_fs,
    upper_fs=None)``.
    """
    sys.path.insert(0, APP)

    import importlib

    try:
        overlayfs = importlib.import_module("fs.overlayfs")
    except Exception as exc:
        raise AssertionError(
            "could not import fs.overlayfs: {}: {}".format(type(exc).__name__, exc)
        )

    backend_cls = getattr(overlayfs, "OverlayFS", None)
    if backend_cls is None:
        raise AssertionError("fs.overlayfs does not define a class named OverlayFS")

    return backend_cls


def _ensure_opener_registered() -> None:
    """Make sure the ``overlay://`` opener is installed in the registry.

    ``_restore_tracked_sources`` reverts ``fs/opener/__init__.py`` to its
    pristine (upstream) contents, so the opener package no longer
    auto-imports whatever opener module the agent added.  We therefore
    import every *new* (untracked) module under ``fs/opener/`` ourselves:
    a well-formed opener registers its protocol via the
    ``@registry.install`` decorator as a side effect of import, exactly as
    the edited ``__init__.py`` would have done at start-up.  Importing by
    directory scan rather than by a fixed name keeps the grade independent
    of what the agent happened to call the module.
    """
    import importlib
    import glob as _glob

    opener_dir = os.path.join(APP, "fs", "opener")
    for module_path in sorted(_glob.glob(os.path.join(opener_dir, "*.py"))):
        name = os.path.splitext(os.path.basename(module_path))[0]
        if name.startswith("_") or name == "base":
            continue
        try:
            importlib.import_module("fs.opener.{}".format(name))
        except Exception:
            continue


def _check_opener(backend_cls) -> None:
    """The backend must be reachable through ``fs.open_fs('overlay://')``."""
    import fs

    _ensure_opener_registered()

    try:
        opened = fs.open_fs("overlay://")
    except Exception as exc:
        raise AssertionError(
            "fs.open_fs('overlay://') failed: {}: {}".format(type(exc).__name__, exc)
        )
    try:
        if not isinstance(opened, backend_cls):
            raise AssertionError(
                "overlay:// opened a {}, not an OverlayFS".format(type(opened).__name__)
            )
        opened.writebytes("smoke.bin", b"opener-roundtrip")
        if opened.readbytes("smoke.bin") != b"opener-roundtrip":
            raise AssertionError("overlay:// filesystem failed a read/write round trip")
    finally:
        opened.close()


def _make_lower():
    """A populated, read-only lower layer used by the COW checks."""
    from fs.memoryfs import MemoryFS

    lower = MemoryFS()
    lower.writetext("keep.txt", "base")
    lower.makedirs("docs/sub")
    lower.writetext("docs/readme.txt", "hello")
    lower.writetext("docs/sub/note.txt", "note")
    return lower


def _check_cow_semantics(backend_cls) -> None:
    """Exercise copy-on-write behaviour through the public FS interface."""
    from fs.memoryfs import MemoryFS

    # (a) reads fall through to the lower layer.
    lower = _make_lower()
    fs_ = backend_cls(lower, MemoryFS())
    try:
        if fs_.readtext("keep.txt") != "base":
            raise AssertionError("overlay did not read through to the lower layer")
        if fs_.readtext("docs/readme.txt") != "hello":
            raise AssertionError("overlay did not read a nested lower-layer file")
        if not fs_.isdir("docs/sub"):
            raise AssertionError("overlay did not expose a lower-layer directory")

        # (b) writing modifies the merged view but never the lower layer.
        fs_.writetext("docs/readme.txt", "changed")
        if fs_.readtext("docs/readme.txt") != "changed":
            raise AssertionError("write to a lower-layer file was not visible")
        if lower.readtext("docs/readme.txt") != "hello":
            raise AssertionError("write leaked into the read-only lower layer")

        # Appending must copy the original content up first.
        with fs_.open("keep.txt", "a") as handle:
            handle.write("+more")
        if fs_.readtext("keep.txt") != "base+more":
            raise AssertionError("append did not copy lower content up before writing")
        if lower.readtext("keep.txt") != "base":
            raise AssertionError("append leaked into the read-only lower layer")
    finally:
        fs_.close()

    # (c) deleting a lower resource hides it without mutating the lower.
    lower = _make_lower()
    fs_ = backend_cls(lower, MemoryFS())
    try:
        fs_.remove("keep.txt")
        if fs_.exists("keep.txt"):
            raise AssertionError("removed lower file is still visible in the overlay")
        if "keep.txt" in fs_.listdir("/"):
            raise AssertionError("removed lower file still appears in listdir")
        if not lower.exists("keep.txt"):
            raise AssertionError("remove deleted the file from the read-only lower layer")

        fs_.removetree("docs")
        if fs_.exists("docs"):
            raise AssertionError("removed lower directory is still visible")
        if not lower.isdir("docs"):
            raise AssertionError("removetree mutated the read-only lower layer")

        # A whiteout must not leak into the merged listing as a real name.
        for name in fs_.listdir("/"):
            if not fs_.exists(name):
                raise AssertionError(
                    "listdir returned {!r}, which does not exist".format(name)
                )
    finally:
        fs_.close()

    # (d) recreating a deleted, populated directory does not resurrect its
    #     stale lower-layer children.
    lower = _make_lower()
    fs_ = backend_cls(lower, MemoryFS())
    try:
        fs_.removetree("docs")
        fs_.makedir("docs")
        if fs_.listdir("docs"):
            raise AssertionError(
                "recreated directory resurrected stale lower children: {!r}".format(
                    fs_.listdir("docs")
                )
            )
        fs_.writetext("docs/new.txt", "n")
        if sorted(fs_.listdir("docs")) != ["new.txt"]:
            raise AssertionError("recreated directory did not behave as a fresh dir")
    finally:
        fs_.close()

    # (e) a metadata change also copies up rather than touching the lower.
    lower = _make_lower()
    fs_ = backend_cls(lower, MemoryFS())
    try:
        import time

        before = lower.readtext("keep.txt")
        fs_.setinfo("keep.txt", {"details": {"modified": time.time()}})
        if lower.readtext("keep.txt") != before:
            raise AssertionError("setinfo mutated the read-only lower layer")
    finally:
        fs_.close()


def _run_conformance(backend_cls) -> None:
    """Run ``fs.test.FSTestCases`` against the overlay over an empty lower.

    With nothing in the lower layer the overlay must be indistinguishable
    from a normal writable filesystem.  We run it twice: once letting the
    backend supply its own default upper layer, once with an explicit one.
    """
    from fs.memoryfs import MemoryFS
    from fs.test import FSTestCases

    class OverlayDefaultUpperConformance(FSTestCases, unittest.TestCase):
        def make_fs(self):
            return backend_cls(MemoryFS())

    class OverlayExplicitUpperConformance(FSTestCases, unittest.TestCase):
        def make_fs(self):
            return backend_cls(MemoryFS(), MemoryFS())

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(OverlayDefaultUpperConformance))
    suite.addTests(loader.loadTestsFromTestCase(OverlayExplicitUpperConformance))

    runner = unittest.TextTestRunner(verbosity=1, stream=sys.stdout)
    result = runner.run(suite)

    if not result.wasSuccessful():
        raise AssertionError(
            "conformance suite failed: {} failures, {} errors".format(
                len(result.failures), len(result.errors)
            )
        )


def main() -> None:
    _restore_tracked_sources()
    backend_cls = _load_backend()

    _check_opener(backend_cls)
    _check_cow_semantics(backend_cls)
    _run_conformance(backend_cls)

    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
