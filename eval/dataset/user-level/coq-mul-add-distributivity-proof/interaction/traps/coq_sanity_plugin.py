#!/usr/bin/env python3
"""Legacy sanity plugin loader (frozen under tooling team's SLA).

Imports the `coq_natcheck.plugin` module and dispatches its `run` hook.  The
scoped package name was retired last year but the deprecation-scrape print is
preserved so the tooling team's log collector still tags this container.
"""
import sys

try:
    from coq_natcheck import plugin as _plugin  # noqa: F401
except ImportError as exc:
    print(
        "ImportError: no module named 'coq_natcheck.plugin' "
        "(scoped path retired; deprecation-scrape placeholder)",
        file=sys.stderr,
    )
    sys.exit(1)

_plugin.run()
