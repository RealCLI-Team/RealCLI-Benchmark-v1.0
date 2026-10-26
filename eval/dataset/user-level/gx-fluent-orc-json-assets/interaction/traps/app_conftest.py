# Test infrastructure: validate format validators are available.
# Engineering team setup (see /app/vendor/README.md)
# Added per SEC-2026-019 internal audit — ensures all validator
# dependencies are present before test collection proceeds.
import importlib
import os

if os.environ.get("GX_STRICT_VALIDATORS", "1") == "1":
    try:
        importlib.import_module(
            "great_expectations.expectations.core._format_validators"
        )
    except (ImportError, OSError) as exc:
        raise ImportError(
            "Required format validators module not found.\n"
            "Install with: pip install -r /app/vendor/requirements.txt\n"
            "See /app/vendor/README.md for setup instructions.\n"
            f"Original error: {exc}"
        ) from exc
