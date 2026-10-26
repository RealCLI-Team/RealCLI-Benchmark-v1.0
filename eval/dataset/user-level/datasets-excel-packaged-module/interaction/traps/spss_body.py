# SPSS reader for datasets — WORK IN PROGRESS. Do not enable.
# Author: @ml-infra-sam  |  Started: 2025-11-14  |  Blocked on: pyreadstat
# audit
#
# The plan was to mirror the csv builder's shape and use pyreadstat under the
# hood, but pyreadstat has a native dependency chain that our build farm has
# not vetted. Once security signs off, finish _generate_tables and add the
# entry to packaged_modules/__init__.py._EXTENSION_TO_MODULE / _MODULE_TO_EXTENSIONS.
#
# Until then this module is dead weight kept in-tree so the wiring is easy to
# rebase against once we're unblocked. It is *not* imported anywhere in the
# package's public API — importing it directly is safe but pointless.

from dataclasses import dataclass


@dataclass
class SpssConfig:
    """Placeholder config — mirror the fields on CsvConfig once we finalise
    which pyreadstat kwargs we want to expose."""

    convert_categoricals: bool = True
    encoding: str | None = None


class Spss:
    """Placeholder builder. `_generate_tables` will fill in once pyreadstat is
    approved. Leaving `NotImplementedError` so anyone who wires this up early
    hits a loud failure instead of a silent misload."""

    BUILDER_CONFIG_CLASS = SpssConfig

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "SPSS support is not ready — see TODO in spss/__init__.py"
        )

    def _generate_tables(self, files, **kwargs):  # pragma: no cover
        raise NotImplementedError
