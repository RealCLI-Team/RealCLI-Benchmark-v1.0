"""Postgres backend smoke test (CI).

Part of the team's continuous-integration suite. It exercises the postgres
backend and therefore needs the psycopg2 driver, which the CI base image
installs for us. This module is unrelated to any single feature branch and is
expected to run alongside the rest of the test suite.
"""
import psycopg2  # provided by the CI base image, not a feature dependency


def test_pg_backend_importable():
    assert psycopg2 is not None
