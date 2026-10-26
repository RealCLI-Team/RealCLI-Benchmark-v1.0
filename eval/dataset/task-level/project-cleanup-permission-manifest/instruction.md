You are working in `/app`. Clean and repair `/app/project`.

Delete only these cleanup targets:

- the `/app/project/cache` directory itself, including everything under it
- the `/app/project/build` directory itself, including everything under it
- `/app/project/logs/old.log`

Do not delete or modify anything under `/app/project/backup`. Also leave permissions under `/app/project/backup` unchanged.

Repair permissions outside deleted paths and outside `/app/project/backup`:

1. Directories: `755`.
2. Shell scripts ending in `.sh`: `755`.
3. Files under `secrets/` and files ending in `.env`: `600`.
4. All other regular files: `644`.

The `/app/project` root directory itself is excluded from repair: leave its permissions unchanged, and count only its subpaths in `permission_fixes`.

Write `/app/cleanup_manifest.json` as an object with `deleted`, `permission_fixes`, `ignored`, and `summary`. The `summary` object must contain the keys `deleted_count` (number of deleted files) and `permission_fix_count` (number of repaired paths). Paths are relative to `/app/project`. List each deleted file in `deleted` (for example `cache/a.tmp`), not the removed directories themselves. In `ignored`, list the paths you intentionally skip and leave unchanged, and state why they were not repaired — for example `backup/archive/cache.tmp`, kept untouched because nothing under `/app/project/backup` may be deleted or modified. Include old and new mode for permission fixes. Sort arrays by path. End JSON with a newline.
