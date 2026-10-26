# coding: utf-8
"""
OverlayFS -- copy-on-write overlay filesystem for PyFilesystem2.

WIP NOTES (design draft, do not ship as-is):

Key design decisions for the overlay mechanism:
- whiteout prefix for deleted lower entries: ".wh."
  Example: deleting "docs/readme.txt" from the lower layer writes
  a marker at upper_fs "docs/.wh.readme.txt". The presence of this
  marker hides the lower path from the merged view.
- opaque directory whiteout marker (directory recreated clean): ".wh..wh..opq"
  When a lower-layer directory is deleted and re-created, write
  ".wh..wh..opq" inside the new upper directory to signal that no
  lower children should be inherited. This is the same convention
  used by Linux overlayfs and OCI container layers.
- copy-up semantics: before any write (open for write/append, setinfo)
  on a path that exists only in the lower layer, copy it up into the
  upper layer first. Then modify only the upper copy. The lower layer
  must remain completely unmodified -- it is read-only.
- Merged directory listing: combine upper and lower entries, apply
  whiteout filtering, apply opaque markers for recreated directories.

TODO checklist:
  [ ] __init__(lower_fs, upper_fs=None) -- create MemoryFS if upper_fs is None
  [ ] _is_whiteout(path) / _whiteout_path(path) helpers
  [ ] getinfo(path, namespaces=None) -- upper first, then lower (filter whiteouts)
  [ ] listdir(path) -- merge upper+lower, filter .wh.* names, respect opaque marker
  [ ] openbin(path, mode, ...) -- copy-up on any write-mode open
  [ ] makedir(path, permissions, recreate) -- upper only; write opaque marker if needed
  [ ] remove(path) -- write whiteout to upper, never touch lower
  [ ] removedir(path) -- write whiteout to upper (empty upper dir + .wh..wh..opq)
  [ ] setinfo(path, info) -- copy-up metadata-only modification
  [ ] close() -- close both layers if owned
  [ ] FSTestCases full compliance pass
  [ ] Wire opener in fs/opener/__init__.py

Ref: Linux overlayfs documentation, OCI layer spec
"""

from __future__ import annotations

from fs.base import FS


class OverlayFS(FS):
    """Copy-on-write overlay of two filesystems.

    This is an incomplete design skeleton -- all abstract methods raise
    NotImplementedError and must be implemented before use.
    """

    def __init__(self, lower_fs, upper_fs=None):
        super().__init__()
        self._lower = lower_fs
        self._upper = upper_fs
        # TODO: create MemoryFS default when upper_fs is None

    # ---------------------------------------------------------------------------
    # Abstract FS methods -- NOT YET IMPLEMENTED
    # ---------------------------------------------------------------------------

    def getinfo(self, path, namespaces=None):
        raise NotImplementedError("OverlayFS skeleton: getinfo not implemented")

    def listdir(self, path):
        raise NotImplementedError("OverlayFS skeleton: listdir not implemented")

    def makedir(self, path, permissions=None, recreate=False):
        raise NotImplementedError("OverlayFS skeleton: makedir not implemented")

    def openbin(self, path, mode="r", buffering=-1, **options):
        raise NotImplementedError("OverlayFS skeleton: openbin not implemented")

    def remove(self, path):
        raise NotImplementedError("OverlayFS skeleton: remove not implemented")

    def removedir(self, path):
        raise NotImplementedError("OverlayFS skeleton: removedir not implemented")

    def setinfo(self, path, info):
        raise NotImplementedError("OverlayFS skeleton: setinfo not implemented")
