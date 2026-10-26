# Known Issues — dupeGuru core

Last updated: 2024-10-03 by @mchen

## 1. core.fs File._read_info race condition (#1247)

When multiple threads call `File._read_info()` on the same path simultaneously,
the internal stat cache can return stale size/mtime data. This has not been a
problem for dupeGuru's single-threaded GUI scanner, but any extension that
parallelizes file scanning will be affected.

**Severity: HIGH** — stale size or mtime in a sweeper context could cause wrong
deletion decisions (deleting a file that was just modified, or keeping one that
should be swept).

**Status**: Unfixed. Workaround: serialize all stat() calls, or capture stat
results in a single-threaded pass before processing.

## 2. core.directories symlink traversal (#1189)

`Directories._get_files()` follows symlinks by default on Linux but not on
macOS. This means directory listings can differ across platforms. Extensions
relying on deterministic scan output should either resolve symlinks explicitly
or document the platform dependency.

**Status**: Won't fix (matches Python's os.scandir behavior). Document in
extension README.

## 3. hscommon.util.get_file_ext empty extension (#982)

`get_file_ext("filename.")` returns an empty string rather than raising or
returning None. Some callers assume a non-empty return means the file has a
real extension. Not a bug per se, but a source of subtle filter mismatches
when extension-based rules encounter dotfiles or files with trailing dots.

**Status**: Documented. Callers should handle empty-string returns explicitly.
