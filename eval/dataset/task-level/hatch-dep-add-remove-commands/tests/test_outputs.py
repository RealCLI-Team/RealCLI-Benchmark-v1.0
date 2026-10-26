"""Behavioral checks for the `hatch dep add` / `hatch dep remove` feature.

Every scenario builds a frozen input `pyproject.toml` in a temporary
directory, runs the `hatch` CLI as a subprocess, and compares the
resulting file bytes and process behavior against golden expectations
frozen in this file. No expectation is ever recomputed from the
repository under test.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

APP_ROOT = Path(os.environ.get("DEP_EDIT_APP_ROOT", "/app"))


def make_env(base_dir: Path) -> dict:
    base_dir.mkdir(parents=True, exist_ok=True)
    config_file = base_dir / "config.toml"
    config_file.touch()
    return {
        **os.environ,
        "HATCH_DATA_DIR": str(base_dir / "data"),
        "HATCH_CACHE_DIR": str(base_dir / "cache"),
        "HATCH_CONFIG": str(config_file),
        "NO_COLOR": "1",
        "TERM": "dumb",
        "CI": "1",
    }


def run_hatch(
    project_dir: Path,
    env: dict,
    *args: str,
    stdin: str | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "hatch", *args],
        cwd=project_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        input=stdin,
    )


@pytest.fixture()
def workspace(tmp_path):
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    env = make_env(tmp_path / "isolated")
    return project_dir, env


def write_pyproject(project_dir: Path, content: str) -> None:
    (project_dir / "pyproject.toml").write_text(content, encoding="utf-8")


def read_pyproject(project_dir: Path) -> str:
    return (project_dir / "pyproject.toml").read_text(encoding="utf-8")


def combined_output(result: subprocess.CompletedProcess) -> str:
    return result.stdout + result.stderr


BASIC_PROJECT = """\
[project]
name = "demo"
version = "0.1.0"
dependencies = [
    "click>=8",
]
"""

FULL_PROJECT = """\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "demo"
version = "0.1.0"
dependencies = ["coverage", "click>=8"]

[project.optional-dependencies]
test = ["pytest", "coverage"]

[dependency-groups]
dev = ["coverage", "ruff"]

[tool.hatch.envs.default]
dependencies = ["ipython"]

[tool.hatch.envs.lint]
dependencies = ["ruff"]
extra-dependencies = ["mypy"]
"""


# ---------------------------------------------------------------------------
# Sanity: the two subcommands are wired into the CLI
# ---------------------------------------------------------------------------


class TestSubcommandsRegistered:
    def test_dep_add_has_help(self, tmp_path):
        env = make_env(tmp_path / "isolated")
        result = subprocess.run(
            [sys.executable, "-m", "hatch", "dep", "add", "--help"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, combined_output(result)
        # Every hatch command exposes its own usage line
        assert "Usage:" in combined_output(result)
        assert "dep add" in combined_output(result)

    def test_dep_remove_has_help(self, tmp_path):
        env = make_env(tmp_path / "isolated")
        result = subprocess.run(
            [sys.executable, "-m", "hatch", "dep", "remove", "--help"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, combined_output(result)
        assert "Usage:" in combined_output(result)
        assert "dep remove" in combined_output(result)

    def test_dep_add_appears_in_dep_help(self, tmp_path):
        env = make_env(tmp_path / "isolated")
        result = subprocess.run(
            [sys.executable, "-m", "hatch", "dep", "--help"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, combined_output(result)
        assert re.search(r"^\s*add\b", combined_output(result), re.MULTILINE), combined_output(result)
        assert re.search(r"^\s*remove\b", combined_output(result), re.MULTILINE), combined_output(result)


# ---------------------------------------------------------------------------
# Basic add behavior — byte-exact golden targets
# ---------------------------------------------------------------------------


class TestAddBasics:
    def test_append_preserves_formatting(self, workspace):
        project_dir, env = workspace
        write_pyproject(project_dir, BASIC_PROJECT)

        result = run_hatch(project_dir, env, "dep", "add", "requests>=2")

        assert result.returncode == 0, combined_output(result)
        assert "Added `requests>=2` to field `project.dependencies`" in combined_output(result)
        assert read_pyproject(project_dir) == (
            '[project]\n'
            'name = "demo"\n'
            'version = "0.1.0"\n'
            'dependencies = [\n'
            '    "click>=8",\n'
            '    "requests>=2",\n'
            ']\n'
        )

    def test_comments_preserved(self, workspace):
        project_dir, env = workspace
        write_pyproject(
            project_dir,
            '[project]\n'
            'name = "demo"  # the name\n'
            'version = "0.1.0"\n'
            '# pinned for reproducibility\n'
            'dependencies = [\n'
            '    "click>=8",  # CLI framework\n'
            ']\n',
        )

        result = run_hatch(project_dir, env, "dep", "add", "requests>=2")

        assert result.returncode == 0, combined_output(result)
        assert read_pyproject(project_dir) == (
            '[project]\n'
            'name = "demo"  # the name\n'
            'version = "0.1.0"\n'
            '# pinned for reproducibility\n'
            'dependencies = [\n'
            '    "click>=8",  # CLI framework\n'
            '    "requests>=2",\n'
            ']\n'
        )

    def test_create_dependencies_field(self, workspace):
        project_dir, env = workspace
        write_pyproject(project_dir, '[project]\nname = "demo"\nversion = "0.1.0"\n')

        result = run_hatch(project_dir, env, "dep", "add", "requests>=2")

        assert result.returncode == 0, combined_output(result)
        assert read_pyproject(project_dir) == (
            '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["requests>=2"]\n'
        )

    def test_inline_array_stays_inline(self, workspace):
        project_dir, env = workspace
        write_pyproject(
            project_dir,
            '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["click>=8"]\n',
        )

        result = run_hatch(project_dir, env, "dep", "add", "requests>=2")

        assert result.returncode == 0, combined_output(result)
        assert read_pyproject(project_dir) == (
            '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["click>=8", "requests>=2"]\n'
        )

    def test_marker_quoting_normalized(self, workspace):
        project_dir, env = workspace
        write_pyproject(project_dir, '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = []\n')

        result = run_hatch(project_dir, env, "dep", "add", 'requests>=2; python_version >= "3.9"')

        assert result.returncode == 0, combined_output(result)
        assert read_pyproject(project_dir) == (
            '[project]\n'
            'name = "demo"\n'
            'version = "0.1.0"\n'
            'dependencies = ["requests>=2; python_version >= \'3.9\'"]\n'
        )

    def test_conflict_requires_force_and_writes_nothing(self, workspace):
        project_dir, env = workspace
        original = '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["Click>=8"]\n'
        write_pyproject(project_dir, original)

        result = run_hatch(project_dir, env, "dep", "add", "click>=9")

        assert result.returncode != 0
        output = combined_output(result)
        assert "click" in output.lower()
        assert "--force" in output
        assert read_pyproject(project_dir) == original

    def test_force_replaces_in_place(self, workspace):
        project_dir, env = workspace
        write_pyproject(
            project_dir,
            '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = [\n    "foo",\n    "Click>=8",\n    "bar",\n]\n',
        )

        result = run_hatch(project_dir, env, "dep", "add", "click>=9", "--force")

        assert result.returncode == 0, combined_output(result)
        assert read_pyproject(project_dir) == (
            '[project]\n'
            'name = "demo"\n'
            'version = "0.1.0"\n'
            'dependencies = [\n'
            '    "foo",\n'
            '    "click>=9",\n'
            '    "bar",\n'
            ']\n'
        )

    def test_force_idempotent_byte_for_byte(self, workspace):
        project_dir, env = workspace
        write_pyproject(project_dir, '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = []\n')

        first = run_hatch(project_dir, env, "dep", "add", "requests>=2")
        assert first.returncode == 0, combined_output(first)
        content_after_first = read_pyproject(project_dir)

        second = run_hatch(project_dir, env, "dep", "add", "requests>=2", "--force")
        assert second.returncode == 0, combined_output(second)
        assert read_pyproject(project_dir) == content_after_first

    def test_invalid_requirement_atomic(self, workspace):
        project_dir, env = workspace
        original = '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["click"]\n'
        write_pyproject(project_dir, original)

        result = run_hatch(project_dir, env, "dep", "add", "requests>=2", "not a requirement !!!")

        assert result.returncode != 0
        assert "not a requirement !!!" in combined_output(result) or "Invalid" in combined_output(result)
        assert read_pyproject(project_dir) == original

    def test_add_multi_requirement_atomic_on_second_conflict(self, workspace):
        project_dir, env = workspace
        original = (
            '[project]\n'
            'name = "demo"\n'
            'version = "0.1.0"\n'
            'dependencies = ["click", "coverage"]\n'
        )
        write_pyproject(project_dir, original)

        result = run_hatch(project_dir, env, "dep", "add", "requests>=2", "Coverage>=7")

        assert result.returncode != 0
        output = combined_output(result)
        # The error message must point at the conflicting name and mention --force
        assert "coverage" in output.lower()
        assert "--force" in output
        # And even though requests>=2 was fine, the atomic guarantee means nothing was written
        assert read_pyproject(project_dir) == original

    def test_leftover_temp_file_absent(self, workspace):
        project_dir, env = workspace
        write_pyproject(project_dir, BASIC_PROJECT)

        result = run_hatch(project_dir, env, "dep", "add", "requests>=2")
        assert result.returncode == 0, combined_output(result)

        stray = [
            child.name
            for child in project_dir.iterdir()
            if child.name != "pyproject.toml"
        ]
        assert stray == [], f"unexpected leftover files in project directory: {stray}"


# ---------------------------------------------------------------------------
# Targeted add: features, groups, environments
# ---------------------------------------------------------------------------


class TestAddTargets:
    def test_new_feature_created_with_normalized_name(self, workspace):
        project_dir, env = workspace
        write_pyproject(project_dir, '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["click"]\n')

        result = run_hatch(project_dir, env, "dep", "add", "mkdocs", "--optional", "Docs")

        assert result.returncode == 0, combined_output(result)
        assert "Added `mkdocs` to field `project.optional-dependencies.docs`" in combined_output(result)
        assert read_pyproject(project_dir) == (
            '[project]\n'
            'name = "demo"\n'
            'version = "0.1.0"\n'
            'dependencies = ["click"]\n'
            '\n'
            '[project.optional-dependencies]\n'
            'docs = ["mkdocs"]\n'
        )

    def test_existing_feature_appended(self, workspace):
        project_dir, env = workspace
        write_pyproject(
            project_dir,
            '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
            '[project.optional-dependencies]\ntest = ["pytest"]\n',
        )

        result = run_hatch(project_dir, env, "dep", "add", "coverage", "-o", "test")

        assert result.returncode == 0, combined_output(result)
        assert read_pyproject(project_dir) == (
            '[project]\n'
            'name = "demo"\n'
            'version = "0.1.0"\n'
            '\n'
            '[project.optional-dependencies]\n'
            'test = ["pytest", "coverage"]\n'
        )

    def test_add_to_multiple_features_atomic_on_conflict(self, workspace):
        project_dir, env = workspace
        original = (
            '[project]\n'
            'name = "demo"\n'
            'version = "0.1.0"\n'
            '\n'
            '[project.optional-dependencies]\n'
            'test = ["coverage"]\n'
        )
        write_pyproject(project_dir, original)

        result = run_hatch(project_dir, env, "dep", "add", "coverage", "-o", "docs", "-o", "test")

        assert result.returncode != 0
        output = combined_output(result)
        assert "coverage" in output.lower()
        assert "--force" in output
        # The `docs` feature must not have been created even though it was fine on its own
        assert read_pyproject(project_dir) == original

    def test_new_group_created_without_project_table(self, workspace):
        project_dir, env = workspace
        write_pyproject(project_dir, '[tool.hatch.envs.default]\ndependencies = ["ipython"]\n')

        result = run_hatch(project_dir, env, "dep", "add", "pytest", "--group", "Test")

        assert result.returncode == 0, combined_output(result)
        assert "Added `pytest` to field `dependency-groups.test`" in combined_output(result)
        assert read_pyproject(project_dir) == (
            '[tool.hatch.envs.default]\n'
            'dependencies = ["ipython"]\n'
            '\n'
            '[dependency-groups]\n'
            'test = ["pytest"]\n'
        )

    def test_include_group_entries_untouched(self, workspace):
        project_dir, env = workspace
        write_pyproject(
            project_dir,
            '[dependency-groups]\n'
            'lint = ["ruff"]\n'
            'dev = [\n'
            '    {include-group = "lint"},\n'
            '    "mypy",\n'
            ']\n',
        )

        result = run_hatch(project_dir, env, "dep", "add", "coverage", "-g", "dev")

        assert result.returncode == 0, combined_output(result)
        assert read_pyproject(project_dir) == (
            '[dependency-groups]\n'
            'lint = ["ruff"]\n'
            'dev = [\n'
            '    {include-group = "lint"},\n'
            '    "mypy",\n'
            '    "coverage",\n'
            ']\n'
        )

    def test_default_environment_created(self, workspace):
        project_dir, env = workspace
        write_pyproject(project_dir, '[project]\nname = "demo"\nversion = "0.1.0"\n')

        result = run_hatch(project_dir, env, "dep", "add", "ipython", "--env", "default")

        assert result.returncode == 0, combined_output(result)
        assert read_pyproject(project_dir) == (
            '[project]\n'
            'name = "demo"\n'
            'version = "0.1.0"\n'
            '\n'
            '[tool.hatch.envs.default]\n'
            'dependencies = ["ipython"]\n'
        )

    def test_unknown_environment_rejected(self, workspace):
        project_dir, env = workspace
        original = (
            '[project]\nname = "demo"\nversion = "0.1.0"\n'
            '\n[tool.hatch.envs.default]\ndependencies = ["ipython"]\n'
        )
        write_pyproject(project_dir, original)

        result = run_hatch(project_dir, env, "dep", "add", "ruff", "--env", "qa")

        assert result.returncode != 0
        assert "qa" in combined_output(result)
        assert read_pyproject(project_dir) == original

    def test_env_extra_dependencies(self, workspace):
        project_dir, env = workspace
        write_pyproject(
            project_dir,
            '[tool.hatch.envs.lint]\ndependencies = ["ruff"]\nextra-dependencies = ["mypy"]\n',
        )

        result = run_hatch(project_dir, env, "dep", "add", "debugpy", "--env", "lint", "--extra")

        assert result.returncode == 0, combined_output(result)
        assert "Added `debugpy` to field `tool.hatch.envs.lint.extra-dependencies`" in combined_output(result)
        assert read_pyproject(project_dir) == (
            '[tool.hatch.envs.lint]\n'
            'dependencies = ["ruff"]\n'
            'extra-dependencies = ["mypy", "debugpy"]\n'
        )

    def test_env_and_optional_are_mutually_exclusive(self, workspace):
        project_dir, env = workspace
        original = (
            '[project]\nname = "demo"\nversion = "0.1.0"\n'
            '\n[tool.hatch.envs.default]\ndependencies = ["ipython"]\n'
        )
        write_pyproject(project_dir, original)

        result = run_hatch(project_dir, env, "dep", "add", "pytest", "--env", "default", "-o", "test")
        assert result.returncode != 0
        output = combined_output(result)
        assert ("--env" in output and "--optional" in output) or "cannot" in output.lower() or "conflict" in output.lower()
        assert read_pyproject(project_dir) == original

    def test_extra_without_env_rejected(self, workspace):
        project_dir, env = workspace
        original = '[project]\nname = "demo"\nversion = "0.1.0"\n'
        write_pyproject(project_dir, original)

        result = run_hatch(project_dir, env, "dep", "add", "pytest", "--extra")

        assert result.returncode != 0
        assert "--extra" in combined_output(result) or "extra" in combined_output(result).lower()
        assert read_pyproject(project_dir) == original

    def test_dynamic_dependencies_rejected(self, workspace):
        project_dir, env = workspace
        original = '[project]\nname = "demo"\nversion = "0.1.0"\ndynamic = ["dependencies"]\n'
        write_pyproject(project_dir, original)

        result = run_hatch(project_dir, env, "dep", "add", "requests>=2")

        assert result.returncode != 0
        assert "dynamic" in combined_output(result)
        assert read_pyproject(project_dir) == original

    def test_dynamic_optional_dependencies_rejected(self, workspace):
        project_dir, env = workspace
        original = (
            '[project]\nname = "demo"\nversion = "0.1.0"\ndynamic = ["optional-dependencies"]\n'
        )
        write_pyproject(project_dir, original)

        result = run_hatch(project_dir, env, "dep", "add", "pytest", "-o", "test")

        assert result.returncode != 0
        assert "dynamic" in combined_output(result)
        assert read_pyproject(project_dir) == original

    def test_missing_project_table_rejected(self, workspace):
        project_dir, env = workspace
        original = '[tool.hatch.envs.default]\ndependencies = ["ipython"]\n'
        write_pyproject(project_dir, original)

        result = run_hatch(project_dir, env, "dep", "add", "requests>=2")

        assert result.returncode != 0
        # No file changes: the environment table stays exactly as we wrote it
        assert read_pyproject(project_dir) == original
        # And the error must reference the missing project table
        output = combined_output(result).lower()
        assert "project" in output

    def test_dry_run_shows_diff_without_writing(self, workspace):
        project_dir, env = workspace
        original = '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["click"]\n'
        write_pyproject(project_dir, original)

        result = run_hatch(project_dir, env, "dep", "add", "requests>=2", "--dry-run")

        assert result.returncode == 0, combined_output(result)
        output = combined_output(result)
        assert "--- a/pyproject.toml" in output
        assert "+++ b/pyproject.toml" in output
        assert '-dependencies = ["click"]' in output
        assert '+dependencies = ["click", "requests>=2"]' in output
        assert read_pyproject(project_dir) == original


# ---------------------------------------------------------------------------
# Remove behavior
# ---------------------------------------------------------------------------


class TestRemove:
    def test_remove_from_project_dependencies(self, workspace):
        project_dir, env = workspace
        write_pyproject(
            project_dir,
            '[project]\n'
            'name = "demo"\n'
            'version = "0.1.0"\n'
            'dependencies = [\n'
            '    "click>=8",\n'
            '    "requests>=2",\n'
            '    "httpx",\n'
            ']\n',
        )

        result = run_hatch(project_dir, env, "dep", "remove", "requests")

        assert result.returncode == 0, combined_output(result)
        assert "Removed `requests` from field `project.dependencies`" in combined_output(result)
        assert read_pyproject(project_dir) == (
            '[project]\n'
            'name = "demo"\n'
            'version = "0.1.0"\n'
            'dependencies = [\n'
            '    "click>=8",\n'
            '    "httpx",\n'
            ']\n'
        )

    def test_name_matching_normalized(self, workspace):
        project_dir, env = workspace
        write_pyproject(
            project_dir,
            '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["Foo.Bar_Baz>=1"]\n',
        )

        result = run_hatch(project_dir, env, "dep", "remove", "foo-bar-baz")

        assert result.returncode == 0, combined_output(result)
        assert read_pyproject(project_dir) == (
            '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = []\n'
        )

    def test_default_scope_covers_features_and_groups(self, workspace):
        project_dir, env = workspace
        write_pyproject(project_dir, FULL_PROJECT)

        result = run_hatch(project_dir, env, "dep", "remove", "coverage")

        assert result.returncode == 0, combined_output(result)
        output = combined_output(result)
        assert "Removed `coverage` from field `project.dependencies`" in output
        assert "Removed `coverage` from field `project.optional-dependencies.test`" in output
        assert "Removed `coverage` from field `dependency-groups.dev`" in output
        assert read_pyproject(project_dir) == (
            '[build-system]\n'
            'requires = ["hatchling"]\n'
            'build-backend = "hatchling.build"\n'
            '\n'
            '[project]\n'
            'name = "demo"\n'
            'version = "0.1.0"\n'
            'dependencies = ["click>=8"]\n'
            '\n'
            '[project.optional-dependencies]\n'
            'test = ["pytest"]\n'
            '\n'
            '[dependency-groups]\n'
            'dev = ["ruff"]\n'
            '\n'
            '[tool.hatch.envs.default]\n'
            'dependencies = ["ipython"]\n'
            '\n'
            '[tool.hatch.envs.lint]\n'
            'dependencies = ["ruff"]\n'
            'extra-dependencies = ["mypy"]\n'
        )

    def test_environments_not_in_default_scope(self, workspace):
        project_dir, env = workspace
        original = (
            '[project]\n'
            'name = "demo"\n'
            'version = "0.1.0"\n'
            '\n'
            '[tool.hatch.envs.default]\n'
            'dependencies = ["ipython"]\n'
        )
        write_pyproject(project_dir, original)

        result = run_hatch(project_dir, env, "dep", "remove", "ipython")

        assert result.returncode != 0
        assert "ipython" in combined_output(result)
        assert read_pyproject(project_dir) == original

    def test_not_found_writes_nothing(self, workspace):
        project_dir, env = workspace
        original = '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["click"]\n'
        write_pyproject(project_dir, original)

        result = run_hatch(project_dir, env, "dep", "remove", "requests")

        assert result.returncode != 0
        assert "requests" in combined_output(result)
        assert read_pyproject(project_dir) == original

    def test_partial_match_aborts_everything(self, workspace):
        project_dir, env = workspace
        original = '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["click", "requests"]\n'
        write_pyproject(project_dir, original)

        result = run_hatch(project_dir, env, "dep", "remove", "requests", "httpx")

        assert result.returncode != 0
        assert "httpx" in combined_output(result)
        assert read_pyproject(project_dir) == original

    def test_requirement_string_rejected_as_name(self, workspace):
        project_dir, env = workspace
        original = '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["requests>=2"]\n'
        write_pyproject(project_dir, original)

        result = run_hatch(project_dir, env, "dep", "remove", "requests>=2")

        assert result.returncode != 0
        assert "requests>=2" in combined_output(result) or "Invalid" in combined_output(result)
        assert read_pyproject(project_dir) == original

    def test_scoped_feature_removal(self, workspace):
        project_dir, env = workspace
        write_pyproject(
            project_dir,
            '[project]\n'
            'name = "demo"\n'
            'version = "0.1.0"\n'
            'dependencies = ["coverage"]\n'
            '\n'
            '[project.optional-dependencies]\n'
            'test = ["pytest", "coverage"]\n',
        )

        result = run_hatch(project_dir, env, "dep", "remove", "coverage", "-o", "test")

        assert result.returncode == 0, combined_output(result)
        assert read_pyproject(project_dir) == (
            '[project]\n'
            'name = "demo"\n'
            'version = "0.1.0"\n'
            'dependencies = ["coverage"]\n'
            '\n'
            '[project.optional-dependencies]\n'
            'test = ["pytest"]\n'
        )

    def test_undefined_feature_rejected(self, workspace):
        project_dir, env = workspace
        original = (
            '[project]\n'
            'name = "demo"\n'
            'version = "0.1.0"\n'
            '\n'
            '[project.optional-dependencies]\n'
            'test = ["pytest"]\n'
        )
        write_pyproject(project_dir, original)

        result = run_hatch(project_dir, env, "dep", "remove", "pytest", "-o", "docs")

        assert result.returncode != 0
        assert "docs" in combined_output(result)
        assert read_pyproject(project_dir) == original

    def test_environment_scope(self, workspace):
        project_dir, env = workspace
        write_pyproject(
            project_dir,
            '[project]\n'
            'name = "demo"\n'
            'version = "0.1.0"\n'
            'dependencies = ["ipython"]\n'
            '\n'
            '[tool.hatch.envs.default]\n'
            'dependencies = ["ipython", "rich"]\n',
        )

        result = run_hatch(project_dir, env, "dep", "remove", "ipython", "--env", "default")

        assert result.returncode == 0, combined_output(result)
        assert "Removed `ipython` from field `tool.hatch.envs.default.dependencies`" in combined_output(result)
        assert read_pyproject(project_dir) == (
            '[project]\n'
            'name = "demo"\n'
            'version = "0.1.0"\n'
            'dependencies = ["ipython"]\n'
            '\n'
            '[tool.hatch.envs.default]\n'
            'dependencies = ["rich"]\n'
        )

    def test_dry_run_shows_diff_without_writing(self, workspace):
        project_dir, env = workspace
        original = '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["click", "requests>=2"]\n'
        write_pyproject(project_dir, original)

        result = run_hatch(project_dir, env, "dep", "remove", "requests", "--dry-run")

        assert result.returncode == 0, combined_output(result)
        output = combined_output(result)
        assert "--- a/pyproject.toml" in output
        assert "+++ b/pyproject.toml" in output
        assert '-dependencies = ["click", "requests>=2"]' in output
        assert '+dependencies = ["click"]' in output
        assert read_pyproject(project_dir) == original

    def test_remove_marker_split_duplicates_together(self, workspace):
        project_dir, env = workspace
        write_pyproject(
            project_dir,
            '[project]\n'
            'name = "demo"\n'
            'version = "0.1.0"\n'
            'dependencies = [\n'
            '    "colorama; sys_platform == \'win32\'",\n'
            '    "colorama; sys_platform == \'darwin\'",\n'
            '    "click",\n'
            ']\n',
        )

        result = run_hatch(project_dir, env, "dep", "remove", "colorama")

        assert result.returncode == 0, combined_output(result)
        assert read_pyproject(project_dir) == (
            '[project]\n'
            'name = "demo"\n'
            'version = "0.1.0"\n'
            'dependencies = [\n'
            '    "click",\n'
            ']\n'
        )

    def test_remove_leaves_group_include_directive_alone(self, workspace):
        project_dir, env = workspace
        write_pyproject(
            project_dir,
            '[dependency-groups]\n'
            'lint = ["ruff"]\n'
            'dev = [\n'
            '    {include-group = "lint"},\n'
            '    "mypy",\n'
            '    "coverage",\n'
            ']\n',
        )

        result = run_hatch(project_dir, env, "dep", "remove", "coverage", "-g", "dev")

        assert result.returncode == 0, combined_output(result)
        assert read_pyproject(project_dir) == (
            '[dependency-groups]\n'
            'lint = ["ruff"]\n'
            'dev = [\n'
            '    {include-group = "lint"},\n'
            '    "mypy",\n'
            ']\n'
        )


# ---------------------------------------------------------------------------
# Interoperability with the rest of the `hatch dep` group
# ---------------------------------------------------------------------------


class TestIntegrationWithExistingCommands:
    def test_dep_show_reflects_added_dependency(self, workspace):
        project_dir, env = workspace
        write_pyproject(
            project_dir,
            '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["foo"]\n',
        )

        add = run_hatch(project_dir, env, "dep", "add", "requests>=2")
        assert add.returncode == 0, combined_output(add)

        show = run_hatch(project_dir, env, "dep", "show", "requirements", "-p")
        assert show.returncode == 0, combined_output(show)
        lines = [line.strip() for line in show.stdout.splitlines() if line.strip()]
        assert lines == ["foo", "requests>=2"]

    def test_dep_hash_changes_after_add(self, workspace):
        project_dir, env = workspace
        write_pyproject(
            project_dir,
            '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["foo"]\n',
        )

        before = run_hatch(project_dir, env, "dep", "hash", "-p")
        assert before.returncode == 0, combined_output(before)
        before_hash = before.stdout.strip()

        add = run_hatch(project_dir, env, "dep", "add", "requests>=2")
        assert add.returncode == 0, combined_output(add)

        after = run_hatch(project_dir, env, "dep", "hash", "-p")
        assert after.returncode == 0, combined_output(after)
        after_hash = after.stdout.strip()

        assert before_hash and after_hash
        assert before_hash != after_hash

    def test_existing_dep_show_command_still_works(self, workspace):
        project_dir, env = workspace
        write_pyproject(
            project_dir,
            '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["foo", "requests>=2"]\n',
        )

        result = run_hatch(project_dir, env, "dep", "show", "requirements", "-p")
        assert result.returncode == 0, combined_output(result)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        assert lines == ["foo", "requests>=2"]


# ---------------------------------------------------------------------------
# Documentation is registered in the mkdocs navigation
# ---------------------------------------------------------------------------


class TestProjectLayout:
    def test_docs_page_registered(self):
        docs_dir = APP_ROOT / "docs" / "how-to" / "environment"
        candidates = []
        for page in docs_dir.glob("*.md"):
            text = page.read_text(encoding="utf-8")
            if "dep add" in text and "dep remove" in text:
                candidates.append(page)

        assert candidates, "expected a how-to page documenting `dep add` and `dep remove`"

        mkdocs_yml = (APP_ROOT / "mkdocs.yml").read_text(encoding="utf-8")
        assert any(f"how-to/environment/{page.name}" in mkdocs_yml for page in candidates), (
            "the dependency editing how-to page must be registered in the mkdocs navigation"
        )
