#!/usr/bin/env python3
"""Grade the transparent encryption filesystem the agent was asked to build.

Two independent things are checked:

1.  **Conformance.** Wrapped around an empty backing store, ``CryptFS``
    must behave exactly like an ordinary writable filesystem, so we run
    the project's own standard suite (``fs.test.FSTestCases``) against it.
    This is implementation-agnostic: any backend that honours the
    PyFilesystem2 contract passes; a subtly-wrong one fails.  The suite
    also drives large uploads/downloads, seeks, text/binary modes and
    metadata namespaces, so the encryption layer is exercised end to end.

2.  **Encryption-at-rest semantics.** Over a real backing store the
    backend must (a) round-trip content faithfully, (b) never leave
    plaintext in the backing store, (c) report the *logical* (plaintext)
    size through ``getinfo``/``getsize`` rather than the on-disk blob size,
    (d) keep the directory structure visible in the backing store, (e)
    detect a wrong key, a tampered blob and a truncated blob by raising,
    and (f) restore everything when reopened with the same key.  These are
    exercised only through the public ``FS`` interface (plus reads of the
    *backing* store, which is fair game -- that is exactly what an attacker
    with the disk would see); the grade never inspects how the blob or MAC
    is laid out internally, so any correct crypto design is accepted.

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

_KEY = "grader-secret-key"


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
    """Import the agent's crypt backend and return its ``FS`` subclass.

    The backend is expected to live at ``fs/cryptfs.py`` and to expose a
    class named ``CryptFS`` constructed as ``CryptFS(backing_fs, key)``.
    """
    sys.path.insert(0, APP)

    import importlib

    try:
        cryptfs = importlib.import_module("fs.cryptfs")
    except Exception as exc:
        raise AssertionError(
            "could not import fs.cryptfs: {}: {}".format(type(exc).__name__, exc)
        )

    backend_cls = getattr(cryptfs, "CryptFS", None)
    if backend_cls is None:
        raise AssertionError("fs.cryptfs does not define a class named CryptFS")

    return backend_cls


def _ensure_opener_registered() -> None:
    """Make sure the ``crypt://`` opener is installed in the registry.

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
    """The backend must be reachable through ``fs.open_fs('crypt://...')``.

    An in-memory backing store keyed only by the URL credential is enough
    to prove the protocol is wired up and does a real crypto round trip.
    """
    import fs

    _ensure_opener_registered()

    url = "crypt://{}@".format(_KEY)
    try:
        opened = fs.open_fs(url)
    except Exception as exc:
        raise AssertionError(
            "fs.open_fs({!r}) failed: {}: {}".format(url, type(exc).__name__, exc)
        )
    try:
        if not isinstance(opened, backend_cls):
            raise AssertionError(
                "crypt:// opened a {}, not a CryptFS".format(type(opened).__name__)
            )
        opened.writebytes("smoke.bin", b"opener-roundtrip")
        if opened.readbytes("smoke.bin") != b"opener-roundtrip":
            raise AssertionError("crypt:// filesystem failed a read/write round trip")
    finally:
        opened.close()


def _check_encryption_semantics(backend_cls) -> None:
    """Exercise encryption-at-rest behaviour through the public FS interface.

    Reads of the *backing* store are deliberately used: that store is what
    an attacker holding the disk would see, so it is the natural place to
    prove that plaintext never lands there and that sizes and structure
    behave as required.  The blob layout itself is never assumed.
    """
    from fs.memoryfs import MemoryFS

    secret = b"the launch codes are 0000 and the safe word is banana"

    # (a) round trip + (b) no plaintext at rest + (c) logical size + (d)
    #     structure visible in the backing store.
    backing = MemoryFS()
    fs_ = backend_cls(backing, key=_KEY)
    try:
        fs_.makedirs("docs/sub")
        fs_.writebytes("docs/secret.bin", secret)
        fs_.writetext("docs/sub/note.txt", "hello world")
        fs_.writebytes("empty.bin", b"")

        # (a) content round-trips through the crypt layer.
        if fs_.readbytes("docs/secret.bin") != secret:
            raise AssertionError("content did not round-trip through CryptFS")
        if fs_.readtext("docs/sub/note.txt") != "hello world":
            raise AssertionError("nested text file did not round-trip")
        if fs_.readbytes("empty.bin") != b"":
            raise AssertionError("empty file did not round-trip")

        # (d) the directory structure is plainly visible in the backing store.
        if not backing.isdir("docs/sub"):
            raise AssertionError("directory structure is not visible in the backing store")
        if not backing.isfile("docs/secret.bin"):
            raise AssertionError("file is not present as a file in the backing store")
        if sorted(fs_.listdir("docs")) != ["secret.bin", "sub"]:
            raise AssertionError("listdir through CryptFS did not match what was written")

        # (b) but the *bytes* on the backing store must not reveal plaintext.
        stored = backing.readbytes("docs/secret.bin")
        if secret in stored:
            raise AssertionError("plaintext content leaked into the backing store")
        # A recognisable plaintext fragment must not survive either.
        if b"launch codes" in stored or b"banana" in stored:
            raise AssertionError("recognisable plaintext fragment leaked at rest")
        stored_note = backing.readbytes("docs/sub/note.txt")
        if b"hello world" in stored_note:
            raise AssertionError("plaintext leaked into the backing store (text file)")

        # (c) the logical size is the plaintext length, not the blob length.
        if fs_.getsize("docs/secret.bin") != len(secret):
            raise AssertionError(
                "getsize returned {}, expected plaintext length {}".format(
                    fs_.getsize("docs/secret.bin"), len(secret)
                )
            )
        info = fs_.getinfo("docs/secret.bin", namespaces=["details"])
        if info.size != len(secret):
            raise AssertionError(
                "getinfo size {} != plaintext length {}".format(info.size, len(secret))
            )
        if fs_.getsize("empty.bin") != 0:
            raise AssertionError("empty file reported a non-zero logical size")
        # scandir must agree with getinfo about the logical size.
        by_name = {i.name: i for i in fs_.scandir("docs", namespaces=["details"])}
        if by_name["secret.bin"].size != len(secret):
            raise AssertionError("scandir reported the wrong logical size")
        # The blob really is larger than the plaintext (crypto overhead),
        # which is why reporting the raw backing size would be wrong.
        if backing.getsize("docs/secret.bin") <= len(secret):
            raise AssertionError(
                "backing blob is not larger than plaintext; overhead is missing"
            )
    finally:
        fs_.close()

    # (f) persistence: reopening the same backing store with the same key
    #     restores every file and directory.
    #
    # NB: many backends close their backing store when they are closed
    # (the usual ``auto_close`` convention), so once we need to reuse a
    # backing store we must keep the wrapper that owns it alive.  Content
    # is already durable in the backing store the moment ``writebytes`` /
    # ``writetext`` returns -- those flush and close their own file handle
    # -- so we simply do not close the writer wrapper here.
    backing = MemoryFS()
    writer = backend_cls(backing, key=_KEY)
    writer.makedir("d")
    writer.writebytes("d/a.bin", secret)
    writer.writetext("top.txt", "persist me")

    reader = backend_cls(backing, key=_KEY)
    if reader.readbytes("d/a.bin") != secret:
        raise AssertionError("content was not recoverable after reopening")
    if reader.readtext("top.txt") != "persist me":
        raise AssertionError("text content was not recoverable after reopening")
    if not reader.isdir("d"):
        raise AssertionError("directory was not recoverable after reopening")

    # (e1) a wrong key must be rejected, not silently return garbage.
    if not _read_fails(backend_cls(backing, key="the-wrong-key"), "d/a.bin"):
        raise AssertionError("reading with the wrong key did not raise")

    # (e2) tampering with the stored blob must be detected.
    backing = MemoryFS()
    writer = backend_cls(backing, key=_KEY)
    writer.writebytes("f.bin", secret)
    blob = bytearray(backing.readbytes("f.bin"))
    # Flip a byte in the middle of the blob (avoid any fixed header/footer).
    mid = len(blob) // 2
    blob[mid] ^= 0xFF
    backing.writebytes("f.bin", bytes(blob))
    if not _read_fails(backend_cls(backing, key=_KEY), "f.bin"):
        raise AssertionError("a tampered blob was not detected on read")

    # (e3) truncation must also be detected.
    backing = MemoryFS()
    writer = backend_cls(backing, key=_KEY)
    writer.writebytes("g.bin", secret)
    full = backing.readbytes("g.bin")
    backing.writebytes("g.bin", full[: len(full) // 2])
    if not _read_fails(backend_cls(backing, key=_KEY), "g.bin"):
        raise AssertionError("a truncated blob was not detected on read")


def _read_fails(fs_, path) -> bool:
    """Return True iff reading ``path`` through ``fs_`` raises.

    The wrapper is intentionally *not* closed: it may share a backing
    store with other live wrappers, and closing it could close that shared
    store (the usual ``auto_close`` convention).  In this short-lived grader
    process leaking a handful of in-memory wrappers is harmless.
    """
    try:
        fs_.readbytes(path)
        return False
    except Exception:
        return True


def _run_conformance(backend_cls) -> None:
    """Run ``fs.test.FSTestCases`` against CryptFS over an empty backing store.

    With a faithful crypto layer the wrapped filesystem must be
    indistinguishable from a normal writable filesystem across the whole
    contract -- opening modes, seeks, large transfers, metadata, moves and
    error paths included.
    """
    from fs.memoryfs import MemoryFS
    from fs.test import FSTestCases

    class CryptFSConformance(FSTestCases, unittest.TestCase):
        def make_fs(self):
            return backend_cls(MemoryFS(), key="conformance-key")

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(CryptFSConformance)

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
    _check_encryption_semantics(backend_cls)
    _run_conformance(backend_cls)

    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
