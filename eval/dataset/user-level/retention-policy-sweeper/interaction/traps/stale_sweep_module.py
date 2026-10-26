"""core.sweep — retention-policy utilities (DRAFT, 2024-07)

Author: @mchen
Status: abandoned, superseded by ops/cleanup.sh approach.

Design notes (kept for reference):
  - Wanted to wrap core.fs for file snapshots with retention metadata
  - Planned to support age-based, size-based, and pattern-based rules
  - Ran into issues with core.directories state management for
    reference/excluded dirs
  - Decided shell-based approach was simpler for our immediate needs

TODO if someone picks this up again:
  - The SweepConfig class below was never finished
  - Need to handle the allocated_size computation (st_blocks * 512)
  - Should support both delete and trash actions
  - Layered config was going to use deep-merge semantics
"""


class SweepConfig:
    """Placeholder configuration for retention policies.

    Fields:
        roots: list of directories to scan
        max_age_days: evict files older than this
        action: 'delete' or 'trash'
    """
    def __init__(self, roots=None, max_age_days=30, action='delete'):
        self.roots = roots or []
        self.max_age_days = max_age_days
        self.action = action


class SweepResult:
    """Placeholder for sweep results. Never implemented."""
    pass


def quick_sweep(config):
    """One-shot sweep — scan, filter, execute.

    Never implemented. Use ops/cleanup.sh instead.
    """
    raise NotImplementedError("Use ops/cleanup.sh for now")
