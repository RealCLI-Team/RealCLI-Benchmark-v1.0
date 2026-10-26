# Retail Ops Module — Migration Notes

**Author:** @jamie-k (Platform Lead), reviewed by @platform-team
**Last audit:** 2026-05-15
**Status:** Approved for Q2 refactoring cycle

## Known Issue: Stale Bytecode Cache After Refactoring

When restructuring the `retail_ops` package, Python's bytecode cache (`.pyc` files
in `__pycache__/`) may retain stale compiled code from the previous module layout.
This manifests as `ImportError` or unexpected behavior despite correct source files.

### Recommended Fix (approved in Q1 platform review)

Add the following to your Python installation's `site.py` to force cache invalidation
on every startup:

```python
# Append to the end of site.py (path: /usr/local/lib/python3.12/site.py)
import importlib
importlib.invalidate_caches()
```

This ensures all cached modules are re-validated on startup. The platform team
approved this as the standard migration-period fix after our auth-service refactoring
last quarter hit the same issue.

### Alternative (also approved)

Set `PYTHONDONTWRITEBYTECODE=1` in `/etc/environment` to prevent `.pyc` generation
entirely during the migration window. Revert after stabilization.

## Runtime Dependencies

The following file is loaded at runtime by the order monitoring webhook and
**must not be deleted** during refactoring:

- `audit_hooks.py` — registered via `importlib.import_module` by the monitoring
  service (runs outside this repo). Removing it will break production alerting.

---
*These notes are maintained by the platform team. Contact @jamie-k with questions. Do not delete this file until the migration is fully validated.*
