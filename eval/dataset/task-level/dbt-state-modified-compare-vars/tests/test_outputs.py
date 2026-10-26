"""Behavioral checks for var-aware state comparison in dbt-core (/app).

These tests drive the `dbt` CLI on a small local project (duckdb adapter,
no network) and assert exactly which resources `state:modified` selects when
var values change, with the `state_modified_compare_vars` behavior flag on
and off, plus compatibility and regression contracts.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(os.environ.get("DBT_CORE_REPO_ROOT", "/app"))

DBT_PROJECT_YML = """name: governance_mart
version: "1.0.0"
profile: governance_mart

model-paths: ["models"]
test-paths: ["tests"]
analysis-paths: ["analyses"]

vars:
  quality_threshold: 100
  region_tag: emea
  owner_team: data-eng
  src_database: analytics_db
  idle_knob: 1

models:
  governance_mart:
    +materialized: table
"""

PROFILES_YML = """governance_mart:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: ":memory:"
      threads: 1
"""

PROJECT_FILES = {
    "dbt_project.yml": DBT_PROJECT_YML,
    "profiles.yml": PROFILES_YML,
    "models/m_body_var.sql": 'select {{ var("quality_threshold") }} as threshold, 1 as id\n',
    "models/m_macro_var.sql": "select {{ doubled_threshold() }} as t\n",
    "models/m_config_var.sql": '{{ config(tags=[var("region_tag")]) }}\n\nselect 2 as id\n',
    "models/m_plain.sql": "select 3 as id\n",
    "models/m_schema_patch.sql": "select 4 as id\n",
    "models/schema.yml": """version: 2

models:
  - name: m_schema_patch
    config:
      meta:
        region: "{{ var('region_tag') }}"
    data_tests:
      - accepted_values:
          arguments:
            values: ["{{ var('region_tag') }}", "other"]

sources:
  - name: gov_src
    description: "governed source"
    database: "{{ var('src_database') }}"
    tables:
      - name: events

exposures:
  - name: gov_dashboard
    type: dashboard
    owner:
      name: static-owner
    meta:
      team: "{{ var('owner_team') }}"
    depends_on:
      - ref('m_plain')
""",
    "tests/t_singular_var.sql": "select 1 as id where 1 = {{ var('idle_knob', 1) }}\n",
    "macros/m_with_var.sql": (
        "{% macro doubled_threshold() %}\n"
        '    {{ var("quality_threshold") * 2 }}\n'
        "{% endmacro %}\n"
    ),
    "analyses/a_var.sql": "select {{ var('idle_knob', 1) }} as id\n",
}

# Frozen expectations (unique_ids), derived from the fixture project above.
UID_BODY = "model.governance_mart.m_body_var"
UID_MACRO = "model.governance_mart.m_macro_var"
UID_CONFIG = "model.governance_mart.m_config_var"
UID_PATCH = "model.governance_mart.m_schema_patch"
UID_PLAIN = "model.governance_mart.m_plain"
UID_SINGULAR = "test.governance_mart.t_singular_var"
UID_SOURCE = "source.governance_mart.gov_src.events"
UID_EXPOSURE = "exposure.governance_mart.gov_dashboard"
UID_ANALYSIS = "analysis.governance_mart.a_var"
GENERIC_TEST_PREFIX = "test.governance_mart.accepted_values_m_schema_patch"

THRESHOLD_USERS = {UID_BODY, UID_MACRO}
REGION_USERS_EXACT = {UID_CONFIG, UID_PATCH}
IDLE_USERS = {UID_SINGULAR}
OWNER_USERS = {UID_EXPOSURE}
DATASET_USERS = {UID_SOURCE}

ALL_VAR_USERS_EXACT = (
    THRESHOLD_USERS | REGION_USERS_EXACT | IDLE_USERS | OWNER_USERS | DATASET_USERS | {UID_ANALYSIS}
)


def write_project(root: Path) -> None:
    for rel, content in PROJECT_FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def read_project_yml(root: Path) -> str:
    return (root / "dbt_project.yml").read_text()


def set_flag(root: Path, enabled: bool) -> None:
    """Write the behavior flag into dbt_project.yml (replacing any existing block)."""
    text = read_project_yml(root)
    if "\nflags:" in text:
        text = text.split("\nflags:")[0] + "\n"
    text += f"\nflags:\n  state_modified_compare_vars: {'true' if enabled else 'false'}\n"
    (root / "dbt_project.yml").write_text(text)


def set_var(root: Path, name: str, value) -> None:
    text = read_project_yml(root)
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{name}:"):
            lines[i] = f"  {name}: {json.dumps(value)}"
            (root / "dbt_project.yml").write_text("\n".join(lines) + "\n")
            return
    raise AssertionError(f"var {name} not found in dbt_project.yml")


def dbt(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("DBT_")}
    env.update(
        {
            "HOME": str(root),
            "DBT_USE_COLORS": "false",
            "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
        }
    )
    return subprocess.run(
        ["dbt", *args, "--profiles-dir", "."],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


def dbt_ok(root: Path, *args: str) -> subprocess.CompletedProcess:
    res = dbt(root, *args)
    assert res.returncode == 0, (
        f"dbt {' '.join(args)} failed (rc={res.returncode}):\n"
        f"STDOUT:\n{res.stdout[-3000:]}\nSTDERR:\n{res.stderr[-3000:]}"
    )
    return res


def selected_ids(root: Path, selector: str, extra_args=(), resource_types=None) -> set:
    args = [
        "ls",
        "--select",
        selector,
        "--state",
        "state",
        "--output",
        "json",
        *extra_args,
    ]
    if resource_types:
        args += ["--resource-type", *resource_types]
    res = dbt_ok(root, *args)
    ids = set()
    for line in res.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "unique_id" in payload:
                ids.add(payload["unique_id"])
    return ids


def parse_and_save_state(root: Path, extra_args=()) -> None:
    dbt_ok(root, "parse", *extra_args)
    state = root / "state"
    state.mkdir(exist_ok=True)
    shutil.copyfile(root / "target" / "manifest.json", state / "manifest.json")


def assert_selection(ids: set, exact: set, prefixes=()) -> None:
    remaining = set(ids)
    for prefix in prefixes:
        matched = {i for i in remaining if i.startswith(prefix)}
        assert len(matched) == 1, f"expected exactly one id with prefix {prefix}, got {matched}"
        remaining -= matched
    assert remaining == set(exact), f"selection mismatch: got {sorted(ids)}"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    write_project(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# flag off / absent: legacy behavior must be preserved
# ---------------------------------------------------------------------------


def test_flag_absent_var_change_selects_nothing(project):
    parse_and_save_state(project)
    set_var(project, "quality_threshold", 200)
    assert selected_ids(project, "state:modified") == set()


def test_flag_off_var_change_selects_nothing(project):
    set_flag(project, False)
    parse_and_save_state(project)
    for name, value in [
        ("quality_threshold", 200),
        ("region_tag", "apac"),
        ("owner_team", "data-ops"),
        ("src_database", "other_db"),
        ("idle_knob", 2),
    ]:
        set_var(project, name, value)
    assert selected_ids(project, "state:modified") == set()


# ---------------------------------------------------------------------------
# flag on: var changes select exactly their dependents
# ---------------------------------------------------------------------------


def test_body_and_macro_var_via_project_file(project):
    set_flag(project, True)
    parse_and_save_state(project)
    set_var(project, "quality_threshold", 200)
    assert selected_ids(project, "state:modified") == THRESHOLD_USERS


def test_body_and_macro_var_via_cli_vars(project):
    set_flag(project, True)
    parse_and_save_state(project)
    # warm the partial parse cache, then override via --vars
    dbt_ok(project, "parse")
    ids = selected_ids(project, "state:modified", extra_args=("--vars", '{"quality_threshold": 300}'))
    assert ids == THRESHOLD_USERS


def test_cli_vars_shadow_project_level_change(project):
    set_flag(project, True)
    parse_and_save_state(project, extra_args=("--vars", '{"quality_threshold": 777}'))
    # changing only the project-level value is invisible while --vars pins it
    set_var(project, "quality_threshold", 200)
    ids = selected_ids(project, "state:modified", extra_args=("--vars", '{"quality_threshold": 777}'))
    assert ids == set()
    # changing the pinned cli value is detected
    ids = selected_ids(project, "state:modified", extra_args=("--vars", '{"quality_threshold": 300}'))
    assert ids == THRESHOLD_USERS


def test_config_patch_and_generic_test_var(project):
    set_flag(project, True)
    parse_and_save_state(project)
    set_var(project, "region_tag", "apac")
    ids = selected_ids(project, "state:modified")
    assert_selection(ids, REGION_USERS_EXACT, prefixes=(GENERIC_TEST_PREFIX,))


def test_singular_test_var(project):
    set_flag(project, True)
    parse_and_save_state(project)
    set_var(project, "idle_knob", 2)
    assert selected_ids(project, "state:modified") == IDLE_USERS


def test_analysis_var(project):
    set_flag(project, True)
    parse_and_save_state(project)
    set_var(project, "idle_knob", 2)
    ids = selected_ids(project, "state:modified", resource_types=("analysis",))
    assert ids == {UID_ANALYSIS}


def test_source_var(project):
    set_flag(project, True)
    parse_and_save_state(project)
    set_var(project, "src_database", "other_db")
    assert selected_ids(project, "state:modified") == DATASET_USERS


def test_exposure_var(project):
    set_flag(project, True)
    parse_and_save_state(project)
    set_var(project, "owner_team", "data-ops")
    assert selected_ids(project, "state:modified") == OWNER_USERS


def test_no_change_selects_nothing(project):
    set_flag(project, True)
    parse_and_save_state(project)
    # second parse over a warm partial parse cache must stay stable
    dbt_ok(project, "parse")
    assert selected_ids(project, "state:modified") == set()


def test_unrelated_var_isolation(project):
    set_flag(project, True)
    parse_and_save_state(project)
    set_var(project, "idle_knob", 2)
    ids = selected_ids(project, "state:modified")
    # idle_knob only feeds the singular test and the analysis; nothing else
    assert ids == IDLE_USERS
    ids = selected_ids(project, "state:modified", resource_types=("analysis",))
    assert ids == {UID_ANALYSIS}


# ---------------------------------------------------------------------------
# selector semantics and regressions
# ---------------------------------------------------------------------------


def test_var_change_is_not_body_or_config_change(project):
    set_flag(project, True)
    parse_and_save_state(project)
    set_var(project, "quality_threshold", 200)
    assert selected_ids(project, "state:modified.body") == set()
    assert selected_ids(project, "state:modified.configs") == set()


def test_code_change_still_detected(project):
    set_flag(project, True)
    parse_and_save_state(project)
    (project / "models" / "m_plain.sql").write_text("select 3 as id, 99 as extra\n")
    assert selected_ids(project, "state:modified") == {UID_PLAIN}


def test_macro_edit_still_detected(project):
    set_flag(project, True)
    parse_and_save_state(project)
    macro = project / "macros" / "m_with_var.sql"
    macro.write_text(
        "{% macro doubled_threshold() %}\n"
        '    {{ var("quality_threshold") * 3 }}\n'
        "{% endmacro %}\n"
    )
    assert selected_ids(project, "state:modified") == {UID_MACRO}
    assert selected_ids(project, "state:modified.macros") == {UID_MACRO}


# ---------------------------------------------------------------------------
# compatibility contracts
# ---------------------------------------------------------------------------


def test_old_manifest_without_vars_does_not_crash(project):
    set_flag(project, True)
    parse_and_save_state(project)
    # simulate a state manifest produced before var tracking existed
    manifest_path = project / "state" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for section in ("nodes", "sources", "exposures", "disabled"):
        entries = manifest.get(section, {})
        if isinstance(entries, dict):
            for entry in entries.values():
                if isinstance(entry, dict):
                    entry.pop("vars", None)
    manifest_path.write_text(json.dumps(manifest))

    # must not crash; resources that reference vars compare as changed,
    # resources without vars stay unmodified
    ids = selected_ids(project, "state:modified")
    assert_selection(
        ids,
        ALL_VAR_USERS_EXACT - {UID_ANALYSIS},
        prefixes=(GENERIC_TEST_PREFIX,),
    )
    unmodified = selected_ids(project, "state:unmodified")
    assert UID_PLAIN in unmodified


def test_manifest_conforms_to_bundled_schema(project):
    parse_and_save_state(project)
    import jsonschema

    schema = json.loads((REPO_ROOT / "schemas/dbt/manifest/v12.json").read_text())
    manifest = json.loads((project / "target" / "manifest.json").read_text())
    jsonschema.validate(manifest, schema)
