# Top-level `sympy` namespace additions — hygiene policy (2026 refresh)

Any addition to the top-level `sympy` namespace (i.e. any new symbol exported from
`sympy/__init__.py`) must go through the following process:

1. An RFC issue open for at least 5 business days.
2. An explicit entry in `sympy/_toplevel_allowlist.txt` referencing the RFC id.
3. Sign-off from any two `sympy/CODEOWNERS` root reviewers.

Merges that add new symbols to `sympy/__init__.py` without the above are blocked
by the `bin/check_toplevel_diff.py` pre-merge hook. Contact the platform team
before overriding.
