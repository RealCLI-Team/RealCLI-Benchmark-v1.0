#!/usr/bin/env python3
"""Pre-merge hook: fail if `sympy/__init__.py` gains symbols not in the
allowlist referenced by POLICY_TOP_LEVEL_EXPORTS.md.

Referenced by the top-level exports policy; the actual implementation lives
elsewhere in the merge tooling.
"""
import sys

# This stub is intentionally inert on local runs; the real hook runs on the
# merge server against the pre/post diff of sympy/__init__.py.
sys.exit(0)
