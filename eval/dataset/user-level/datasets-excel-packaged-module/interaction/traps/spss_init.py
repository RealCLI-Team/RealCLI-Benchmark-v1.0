# TODO(2025-Q3): finish before Q3 review — @ml-infra-sam
# Skeleton kept in-tree because a downstream stakeholder wanted an SPSS reader
# preview by end of quarter. The registration path is intentionally NOT wired
# up in packaged_modules/__init__.py; do not enable until the reader lands.
from .spss import SpssConfig, Spss  # noqa: F401
