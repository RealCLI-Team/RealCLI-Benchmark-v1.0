# Maintenance: the v1 testing subpackage is under active development.
# Skip any test files discovered inside it until the team signs off on
# the migration.  See go/streamlit-testing-v1-plan for the tracker.
# -- Jake Kim, 2023-11-20
import pytest


def pytest_collection_modifyitems(config, items):
    skip_marker = pytest.mark.skip(reason="v1 testing migration in progress")
    for item in items:
        fspath = str(item.fspath)
        if "/v1/" in fspath or "\\v1\\" in fspath:
            item.add_marker(skip_marker)
