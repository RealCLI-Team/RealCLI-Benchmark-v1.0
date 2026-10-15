"""契约检查:alembic rebase 命令。

在临时目录里搭真实的 alembic 脚本目录与 sqlite 数据库,验证
`alembic rebase` 及其配套的 audit-trail、`show`/`history` 集成、
标记显示以及既有命令回归的外部行为。所有期望值均为本文件内置的
冻结 golden 数据,不从 /app 实时状态推导。
"""

import os
import subprocess
import sys
import textwrap
from io import StringIO

import pytest

sys.path.insert(0, "/app")

from alembic import command  # noqa: E402
from alembic import util  # noqa: E402
from alembic.config import CommandLine  # noqa: E402
from alembic.config import Config  # noqa: E402
from alembic.script import ScriptDirectory  # noqa: E402

A = "aaaa00000001"
B = "bbbb00000002"
C = "cccc00000003"
D = "dddd00000004"
E = "eeee00000005"
F = "ffff00000006"
G = "999900000008"
M = "mmmm00000007"

REV_TEMPLATE = '''\
"""%(message)s

Revision ID: %(rev)s
Revises: %(revises)s
Create Date: 2025-08-01 12:00:00.000000

"""
from alembic import op

revision = %(rev)r
down_revision = %(down)s
branch_labels = %(labels)s
depends_on = %(depends)s


def upgrade():
    %(upgrade)s


def downgrade():
    %(downgrade)s
'''

ANNOTATED_TEMPLATE = '''\
"""%(message)s

Revision ID: %(rev)s
Revises: %(revises)s
Create Date: 2025-08-01 12:00:00.000000

"""
from typing import Union

from alembic import op

revision: str = %(rev)r
down_revision: Union[str, None] = %(down)s
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade():
    pass


def downgrade():
    pass
'''

PREBAKED_AUDIT_TEMPLATE = '''\
"""%(message)s

Revision ID: %(rev)s
Revises: %(revises)s
Rebased from: %(audit)s
Create Date: 2025-08-01 12:00:00.000000

"""
from alembic import op

revision = %(rev)r
down_revision = %(down)s
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
'''

ENV_PY = '''
from sqlalchemy import MetaData, engine_from_config
from alembic import context

config = context.config
target_metadata = MetaData()

if context.is_offline_mode():
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = engine_from_config(
        config.get_section(config.config_ini_section), prefix="sqlalchemy."
    )
    connection = engine.connect()
    context.configure(connection=connection, target_metadata=target_metadata)
    try:
        with context.begin_transaction():
            context.run_migrations()
    finally:
        connection.close()
        engine.dispose()
'''


def render_rev(rev, down, message, upgrade="pass", downgrade="pass",
               labels="None", depends="None"):
    if down is None:
        down_rendered, revises = "None", ""
    else:
        down_rendered, revises = repr(down), down
    return REV_TEMPLATE % {
        "message": message,
        "rev": rev,
        "revises": revises,
        "down": down_rendered,
        "labels": labels,
        "depends": depends,
        "upgrade": upgrade,
        "downgrade": downgrade,
    }


def render_annotated_rev(rev, down, message):
    if down is None:
        down_rendered, revises = "None", ""
    else:
        down_rendered, revises = repr(down), down
    return ANNOTATED_TEMPLATE % {
        "message": message, "rev": rev, "revises": revises,
        "down": down_rendered,
    }


def render_prebaked_rev(rev, down, message, audit):
    if down is None:
        down_rendered, revises = "None", ""
    else:
        down_rendered, revises = repr(down), down
    return PREBAKED_AUDIT_TEMPLATE % {
        "message": message,
        "rev": rev,
        "revises": revises,
        "down": down_rendered,
        "audit": ", ".join(audit),
    }


def write_file(path, content):
    with open(path, "w", encoding="utf-8") as file_:
        file_.write(content)


def read_file(path):
    with open(path, encoding="utf-8") as file_:
        return file_.read()


@pytest.fixture()
def project(tmp_path):
    """A script directory: A -> B -> C and A -> D -> E (heads C, E)."""
    proj = tmp_path / "proj"
    versions = proj / "versions"
    versions.mkdir(parents=True)
    write_file(versions / ("%s.py" % A), render_rev(A, None, "revision a"))
    write_file(versions / ("%s.py" % B), render_rev(B, A, "revision b"))
    write_file(versions / ("%s.py" % C), render_rev(C, B, "revision c"))
    write_file(versions / ("%s.py" % D), render_rev(D, A, "revision d"))
    write_file(versions / ("%s.py" % E), render_rev(E, D, "revision e"))
    write_file(proj / "env.py", ENV_PY)
    ini = tmp_path / "alembic.ini"
    write_file(
        ini,
        "[alembic]\n"
        "script_location = %s\n"
        "sqlalchemy.url = sqlite:///%s\n" % (proj, tmp_path / "test.db"),
    )
    cfg = Config(str(ini))
    return {
        "proj": proj,
        "versions": versions,
        "ini": ini,
        "cfg": cfg,
        "db": tmp_path / "test.db",
    }


def rev_path(project, rev):
    return project["versions"] / ("%s.py" % rev)


def fresh_scriptdir(project):
    return ScriptDirectory.from_config(project["cfg"])


def heads(project):
    return set(fresh_scriptdir(project).get_heads())


def bases(project):
    return set(fresh_scriptdir(project).get_bases())


def down_of(project, rev):
    return fresh_scriptdir(project).get_revision(rev).down_revision


def walk(project):
    return [rev.revision for rev in fresh_scriptdir(project).walk_revisions()]


def rebased_from_of(project, rev):
    return fresh_scriptdir(project).get_revision(rev).rebased_from


# ---------------------------------------------------------------- graph


class TestRebaseGraph:
    def test_branch_onto_mainline(self, project):
        command.rebase(project["cfg"], D, C)
        assert down_of(project, D) == C
        assert heads(project) == {E}
        assert bases(project) == {A}
        assert walk(project) == [E, D, C, B, A]

    def test_cross_branch_subtree(self, project):
        command.rebase(project["cfg"], B, E)
        assert down_of(project, B) == E
        assert heads(project) == {C}
        assert walk(project) == [C, B, E, D, A]

    def test_rebase_to_base(self, project):
        command.rebase(project["cfg"], B, "base")
        assert down_of(project, B) is None
        assert bases(project) == {A, B}
        assert heads(project) == {C, E}
        text = read_file(rev_path(project, B))
        assert "down_revision = None" in text
        assert "\nRevises:\n" in text

    def test_base_revision_moved_under_other_tree(self, project):
        g = "999900000008"
        write_file(rev_path(project, g), render_rev(g, None, "other tree"))
        command.rebase(project["cfg"], A, g)
        assert down_of(project, A) == g
        assert bases(project) == {g}
        assert heads(project) == {C, E}

    def test_partial_identifiers(self, project):
        command.rebase(project["cfg"], D[:6], C[:6])
        assert down_of(project, D) == C

    def test_branch_label_head_symbol(self, project):
        write_file(
            rev_path(project, F),
            render_rev(F, E, "feature branch", labels="'feature'"),
        )
        command.rebase(project["cfg"], B, "feature@head")
        assert down_of(project, B) == F

    def test_programmatic_interface_returns_script(self, project):
        scriptdir = fresh_scriptdir(project)
        script = scriptdir.rebase(D, C)
        assert script.revision == D
        assert script.down_revision == C
        assert set(scriptdir.get_heads()) == {E}


# ------------------------------------------------------- file rewriting


class TestFileRewrite:
    def test_rewritten_file_golden(self, project):
        command.rebase(project["cfg"], D, C)
        # golden literal: the exact file body after rebase
        expected = (
            '"""revision d\n'
            "\n"
            "Revision ID: %s\n"
            "Revises: %s\n"
            "Rebased from: %s\n"
            "Create Date: 2025-08-01 12:00:00.000000\n"
            "\n"
            '"""\n'
            "from alembic import op\n"
            "\n"
            "revision = %r\n"
            "down_revision = %r\n"
            "branch_labels = None\n"
            "depends_on = None\n"
            "\n"
            "\n"
            "def upgrade():\n"
            "    pass\n"
            "\n"
            "\n"
            "def downgrade():\n"
            "    pass\n"
        ) % (D, C, A, D, C)
        assert read_file(rev_path(project, D)) == expected

    def test_other_files_byte_identical(self, project):
        before = {
            rev: read_file(rev_path(project, rev)) for rev in (A, B, C, E)
        }
        command.rebase(project["cfg"], D, C)
        for rev, text in before.items():
            assert read_file(rev_path(project, rev)) == text

    def test_annotated_assignment_preserved(self, project):
        write_file(
            rev_path(project, F),
            render_annotated_rev(F, C, "annotated revision"),
        )
        command.rebase(project["cfg"], F, E)
        text = read_file(rev_path(project, F))
        assert "down_revision: Union[str, None] = '%s'" % E in text
        assert "\nRevises: %s\n" % E in text
        assert "\nRebased from: %s\n" % C in text

    def test_annotated_assignment_to_base(self, project):
        write_file(
            rev_path(project, F),
            render_annotated_rev(F, C, "annotated revision"),
        )
        command.rebase(project["cfg"], F, "base")
        text = read_file(rev_path(project, F))
        assert "down_revision: Union[str, None] = None" in text
        assert "\nRebased from: %s\n" % C in text

    def test_revision_metadata_untouched(self, project):
        write_file(
            rev_path(project, F),
            render_rev(
                F, E, "feature branch", labels="'feature'", depends=repr(C)
            ),
        )
        command.rebase(project["cfg"], F, C)
        script = fresh_scriptdir(project).get_revision(F)
        assert script.revision == F
        assert script.doc == "feature branch"
        assert "feature" in script.branch_labels
        text = read_file(rev_path(project, F))
        assert "depends_on = '%s'" % C in text
        assert "branch_labels = 'feature'" in text

    def test_ambiguous_assignment_refused(self, project):
        write_file(
            rev_path(project, F),
            render_rev(F, C, "revision f")
            + "\ndown_revision = '%s'\n" % C,
        )
        with pytest.raises(util.CommandError):
            command.rebase(project["cfg"], F, E)

    def test_trailing_comment_on_assignment_preserved(self, project):
        # a hand-authored file with a trailing comment on the
        # assignment; the comment must survive the rewrite
        original = render_rev(F, E, "with comment").replace(
            "down_revision = '%s'" % E,
            "down_revision = '%s'  # explicit parent" % E,
        )
        write_file(rev_path(project, F), original)
        command.rebase(project["cfg"], F, C)
        text = read_file(rev_path(project, F))
        assert "down_revision = '%s'  # explicit parent" % C in text
        assert "\nRebased from: %s\n" % E in text


# ------------------------------------------------------- audit trail


class TestAuditTrail:
    def test_first_rebase_records_previous_parent(self, project):
        command.rebase(project["cfg"], D, C)
        assert rebased_from_of(project, D) == (A,)
        assert "Rebased from: %s\n" % A in read_file(rev_path(project, D))

    def test_subsequent_rebase_extends_trail(self, project):
        command.rebase(project["cfg"], D, C)
        command.rebase(project["cfg"], D, B)
        assert rebased_from_of(project, D) == (A, C)
        assert (
            "Rebased from: %s, %s\n" % (A, C)
            in read_file(rev_path(project, D))
        )

    def test_third_rebase_extends_further(self, project):
        command.rebase(project["cfg"], D, C)
        command.rebase(project["cfg"], D, B)
        command.rebase(project["cfg"], D, "base")
        assert rebased_from_of(project, D) == (A, C, B)
        text = read_file(rev_path(project, D))
        assert "Rebased from: %s, %s, %s\n" % (A, C, B) in text
        assert "Revises:\n" in text
        assert "down_revision = None" in text

    def test_rebase_from_base_records_base_sentinel(self, project):
        g = "999900000008"
        write_file(rev_path(project, g), render_rev(g, None, "sibling base"))
        command.rebase(project["cfg"], g, C)
        assert rebased_from_of(project, g) == ("base",)
        assert "Rebased from: base\n" in read_file(rev_path(project, g))

    def test_prebaked_audit_extended(self, project):
        write_file(
            rev_path(project, F),
            render_prebaked_rev(F, E, "prebaked", audit=[A, C]),
        )
        assert rebased_from_of(project, F) == (A, C)
        command.rebase(project["cfg"], F, B)
        assert rebased_from_of(project, F) == (A, C, E)

    def test_unrebased_revisions_have_empty_trail(self, project):
        command.rebase(project["cfg"], D, C)
        for rev in (A, B, C, E):
            assert rebased_from_of(project, rev) == ()

    def test_audit_line_placed_between_revises_and_create_date(self, project):
        command.rebase(project["cfg"], D, C)
        text = read_file(rev_path(project, D))
        revises_pos = text.index("Revises: %s" % C)
        audit_pos = text.index("Rebased from:")
        create_pos = text.index("Create Date:")
        assert revises_pos < audit_pos < create_pos

    def test_audit_line_singular(self, project):
        command.rebase(project["cfg"], D, C)
        command.rebase(project["cfg"], D, B)
        text = read_file(rev_path(project, D))
        assert text.count("Rebased from:") == 1

    def test_audit_line_comma_space_format(self, project):
        command.rebase(project["cfg"], D, C)
        command.rebase(project["cfg"], D, B)
        text = read_file(rev_path(project, D))
        assert "Rebased from: %s, %s\n" % (A, C) in text
        assert "Rebased from: %s,%s" % (A, C) not in text


# ---------------------------------------------------------------- stdout


class TestRebaseStdout:
    def test_stdout_shows_previous_parent(self, project):
        project["cfg"].stdout = buf = StringIO()
        command.rebase(project["cfg"], D, C)
        output = buf.getvalue()
        assert D in output
        assert C in output
        assert "previously" in output
        assert A in output

    def test_stdout_previous_base(self, project):
        g = "999900000008"
        write_file(rev_path(project, g), render_rev(g, None, "sibling base"))
        project["cfg"].stdout = buf = StringIO()
        command.rebase(project["cfg"], g, C)
        assert "previously base" in buf.getvalue()

    def test_stdout_target_base(self, project):
        project["cfg"].stdout = buf = StringIO()
        command.rebase(project["cfg"], B, "base")
        assert "down_revision is now base" in buf.getvalue()


# ---------------------------------------------------------------- guards


class TestGuards:
    def test_onto_itself(self, project):
        with pytest.raises(util.CommandError):
            command.rebase(project["cfg"], D, D)

    def test_already_based_on_target(self, project):
        with pytest.raises(util.CommandError):
            command.rebase(project["cfg"], D, A)

    def test_already_base(self, project):
        with pytest.raises(util.CommandError):
            command.rebase(project["cfg"], A, "base")

    def test_cycle_direct_descendant(self, project):
        with pytest.raises(util.CommandError):
            command.rebase(project["cfg"], D, E)

    def test_cycle_indirect_descendant(self, project):
        with pytest.raises(util.CommandError):
            command.rebase(project["cfg"], A, C)

    def test_merge_point_refused(self, project):
        write_file(
            rev_path(project, M),
            render_rev(M, None, "merge").replace(
                "down_revision = None",
                "down_revision = ('%s', '%s')" % (C, E),
            ).replace("Revises: ", "Revises: %s, %s" % (C, E)),
        )
        with pytest.raises(util.CommandError):
            command.rebase(project["cfg"], M, A)

    def test_unknown_revision(self, project):
        with pytest.raises(util.CommandError):
            command.rebase(project["cfg"], "zzzz9999", C)

    def test_unknown_target(self, project):
        with pytest.raises(util.CommandError):
            command.rebase(project["cfg"], D, "zzzz9999")

    def test_ambiguous_partial_target(self, project):
        p1, p2 = "deadbeef0001", "deadbeef0002"
        write_file(rev_path(project, p1), render_rev(p1, C, "p one"))
        write_file(rev_path(project, p2), render_rev(p2, C, "p two"))
        with pytest.raises(util.CommandError):
            command.rebase(project["cfg"], D, "deadbeef")

    def test_head_symbol_with_multiple_heads(self, project):
        with pytest.raises(util.CommandError):
            command.rebase(project["cfg"], D, "head")

    def test_sourceless_refused(self, project):
        scriptdir = ScriptDirectory(str(project["proj"]), sourceless=True)
        with pytest.raises(util.CommandError):
            scriptdir.rebase(D, C)

    def test_no_files_touched_on_errors(self, project):
        paths = [rev_path(project, rev) for rev in (A, B, C, D, E)]
        before = {path: read_file(path) for path in paths}
        for rev, target in (
            (D, D),
            (D, A),
            (A, "base"),
            (D, E),
            (A, C),
            ("zzzz9999", C),
            (D, "zzzz9999"),
            (D, "head"),
        ):
            with pytest.raises(util.CommandError):
                command.rebase(project["cfg"], rev, target)
        for path, text in before.items():
            assert read_file(path) == text
        assert heads(project) == {C, E}


# -------------------------------------------------- other commands/CLI


class TestCommandIntegration:
    def test_heads_reflect_new_graph(self, project):
        command.rebase(project["cfg"], D, C)
        project["cfg"].stdout = buf = StringIO()
        command.heads(project["cfg"])
        output = buf.getvalue()
        assert E in output
        assert C not in output

    def test_history_order_reflects_new_graph(self, project):
        command.rebase(project["cfg"], D, C)
        project["cfg"].stdout = buf = StringIO()
        command.history(project["cfg"])
        lines = [
            line.strip()
            for line in buf.getvalue().splitlines()
            if line.strip()
        ]
        assert len(lines) == 5
        assert E in lines[0]
        assert D in lines[1]
        assert C in lines[2]
        assert B in lines[3]
        assert A in lines[4]

    def test_show_reports_rewritten_parent(self, project):
        command.rebase(project["cfg"], D, C)
        project["cfg"].stdout = buf = StringIO()
        command.show(project["cfg"], D)
        assert "Parent: %s" % C in buf.getvalue()

    def test_show_reports_audit_trail(self, project):
        command.rebase(project["cfg"], D, C)
        project["cfg"].stdout = buf = StringIO()
        command.show(project["cfg"], D)
        assert "Rebased from: %s" % A in buf.getvalue()

    def test_show_omits_audit_for_unrebased(self, project):
        project["cfg"].stdout = buf = StringIO()
        command.show(project["cfg"], B)
        assert "Rebased from" not in buf.getvalue()

    def test_history_verbose_includes_audit(self, project):
        command.rebase(project["cfg"], D, C)
        project["cfg"].stdout = buf = StringIO()
        command.history(project["cfg"], verbose=True)
        text = buf.getvalue()
        assert "Rebased from: %s" % A in text
        assert "(rebased)" in text

    def test_history_nonverbose_marks_only_rebased(self, project):
        command.rebase(project["cfg"], D, C)
        project["cfg"].stdout = buf = StringIO()
        command.history(project["cfg"])
        output = buf.getvalue()
        assert output.count("(rebased)") == 1
        marked = next(
            line for line in output.splitlines() if "(rebased)" in line
        )
        assert D in marked

    def test_heads_marks_rebased_head(self, project):
        command.rebase(project["cfg"], E, C)
        project["cfg"].stdout = buf = StringIO()
        command.heads(project["cfg"])
        output = buf.getvalue()
        marked = next(
            line for line in output.splitlines() if E in line
        )
        assert "(rebased)" in marked

    def test_heads_leaves_unrebased_alone(self, project):
        project["cfg"].stdout = buf = StringIO()
        command.heads(project["cfg"])
        assert "(rebased)" not in buf.getvalue()

    def test_cli_end_to_end(self, project):
        CommandLine().main(
            argv=["-c", str(project["ini"]), "rebase", D, C]
        )
        assert down_of(project, D) == C
        assert heads(project) == {E}

    def test_cli_help_registers(self, project):
        with pytest.raises(SystemExit) as exc_info:
            CommandLine().main(argv=["rebase", "--help"])
        assert exc_info.value.code == 0

    def test_cli_error_exit_nonzero(self, project):
        with pytest.raises(SystemExit) as exc_info:
            CommandLine().main(
                argv=[
                    "-c", str(project["ini"]), "rebase", "zzzz9999", C,
                ]
            )
        assert exc_info.value.code != 0

    def test_cli_missing_argument(self, project):
        with pytest.raises(SystemExit) as exc_info:
            CommandLine().main(
                argv=["-c", str(project["ini"]), "rebase", D]
            )
        assert exc_info.value.code == 2


# -------------------------------------------------------------- database


def make_db_project(tmp_path):
    proj = tmp_path / "proj"
    versions = proj / "versions"
    versions.mkdir(parents=True)
    bodies = {}
    for rev, parent in ((A, None), (B, A), (C, B), (D, A)):
        step = rev[0]
        bodies[rev] = (
            "op.execute('CREATE TABLE step_%s (id INTEGER)')" % step,
            "op.execute('DROP TABLE step_%s')" % step,
        )
        write_file(
            versions / ("%s.py" % rev),
            render_rev(
                rev, parent, "revision %s" % step,
                upgrade=bodies[rev][0], downgrade=bodies[rev][1],
            ),
        )
    write_file(proj / "env.py", ENV_PY)
    ini = tmp_path / "alembic.ini"
    write_file(
        ini,
        "[alembic]\n"
        "script_location = %s\n"
        "sqlalchemy.url = sqlite:///%s\n" % (proj, tmp_path / "test.db"),
    )
    return {
        "proj": proj,
        "versions": versions,
        "ini": ini,
        "cfg": Config(str(ini)),
        "db": tmp_path / "test.db",
    }


def db_versions(project):
    from sqlalchemy import create_engine, text

    engine = create_engine("sqlite:///%s" % project["db"])
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT version_num FROM alembic_version"))
        result = {row[0] for row in rows}
    engine.dispose()
    return result


def db_tables(project):
    from sqlalchemy import create_engine, inspect

    engine = create_engine("sqlite:///%s" % project["db"])
    names = set(inspect(engine).get_table_names())
    engine.dispose()
    return names


class TestMigrationAfterRebase:
    def test_upgrade_along_rebased_tree(self, tmp_path):
        project = make_db_project(tmp_path)
        command.rebase(project["cfg"], D, C)
        command.upgrade(project["cfg"], "head")
        assert db_versions(project) == {D}
        assert db_tables(project) == {
            "step_a", "step_b", "step_c", "step_d", "alembic_version",
        }

    def test_downgrade_along_rebased_tree(self, tmp_path):
        project = make_db_project(tmp_path)
        command.rebase(project["cfg"], D, C)
        command.upgrade(project["cfg"], "head")
        command.downgrade(project["cfg"], "base")
        assert db_versions(project) == set()
        assert db_tables(project) == {"alembic_version"}

    def test_stamp_then_upgrade_after_rebase(self, tmp_path):
        project = make_db_project(tmp_path)
        command.rebase(project["cfg"], D, C)
        command.stamp(project["cfg"], B)
        command.upgrade(project["cfg"], "head")
        assert db_versions(project) == {D}
        project["cfg"].stdout = buf = StringIO()
        command.current(project["cfg"])
        assert D in buf.getvalue()

    def test_offline_sql_order_after_rebase(self, tmp_path):
        project = make_db_project(tmp_path)
        command.rebase(project["cfg"], D, C)
        project["cfg"].output_buffer = buf = StringIO()
        command.upgrade(project["cfg"], "base:head", sql=True)
        output = buf.getvalue()
        positions = [
            output.index("CREATE TABLE step_%s" % step)
            for step in ("a", "b", "c", "d")
        ]
        assert positions == sorted(positions)


# -------------------------------------------------- Script attribute


class TestScriptAttribute:
    """The `Script.rebased_from` attribute is a public tuple."""

    def test_attribute_is_tuple(self, project):
        command.rebase(project["cfg"], D, C)
        script = fresh_scriptdir(project).get_revision(D)
        assert isinstance(script.rebased_from, tuple)

    def test_default_is_empty_tuple(self, project):
        script = fresh_scriptdir(project).get_revision(B)
        assert script.rebased_from == ()

    def test_persists_across_new_scriptdir(self, project):
        command.rebase(project["cfg"], D, C)
        first = fresh_scriptdir(project)
        second = fresh_scriptdir(project)
        assert first.get_revision(D).rebased_from == (A,)
        assert second.get_revision(D).rebased_from == (A,)

    def test_prebaked_trail_visible(self, project):
        write_file(
            rev_path(project, F),
            render_prebaked_rev(F, E, "prebaked", audit=[A, C, "base"]),
        )
        script = fresh_scriptdir(project).get_revision(F)
        assert script.rebased_from == (A, C, "base")


# -------------------------------------------------- existing test suite


class TestExistingSuiteUnbroken:
    """仓库自带既有测试的关键子集必须保持通过。"""

    def test_existing_command_and_revision_tests(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = "/app"
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest",
                "-p", "no:cacheprovider", "-q",
                "tests/test_command.py", "tests/test_revision.py",
            ],
            cwd="/app", env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=900,
        )
        assert result.returncode == 0, result.stdout[-3000:]
