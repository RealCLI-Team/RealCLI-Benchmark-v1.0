"""Behavioral checks for the `.cruftignore` feature in cruft.

Every check exercises the public command line interface only: local git
template repositories are constructed in temporary directories, projects
are generated from them, templates evolve, and the behavior of
``cruft update`` / ``cruft diff`` / ``cruft create`` / ``cruft check``
is asserted against frozen golden data embedded in this file.  Nothing
touches the network.

The suite deliberately covers not only the happy path but a series of
regression contracts that a shallow implementation tends to trip on:

* the exact ``Skipping ignored path: <path>`` wording, path style and
  alphabetical order in ``cruft update``'s stdout;
* absence of ``.rej`` files anywhere in the project — including on the
  three-way merge code path exercised inside a real git repository;
* absence of ignored path names from the interactive ``v`` view;
* the fact that the diff output is a clean, ``patch``-applyable patch;
* composability with the pre-existing ``.cruft.json`` / ``pyproject``
  ``skip`` configuration;
* pre-existing offline unit tests continue to pass unchanged.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

APP_DIR = os.environ.get("CRUFT_APP_DIR", "/app")

GIT_ENV = {
    "GIT_AUTHOR_NAME": "verifier",
    "GIT_AUTHOR_EMAIL": "verifier@example.com",
    "GIT_COMMITTER_NAME": "verifier",
    "GIT_COMMITTER_EMAIL": "verifier@example.com",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
}


# ---------------------------------------------------------------------
# Frozen template fixtures and golden data
# ---------------------------------------------------------------------

CONTEXT = {"project_slug": "webapp", "author": "Alice"}

CRUFTIGNORE_V1 = (
    "# owned by the generated project\n"
    "config.ini\n"
    "settings/\n"
    "*.log\n"
    "!keep.log\n"
)

FILES_V1 = {
    "README.md": "# {{cookiecutter.project_slug}}\nby {{cookiecutter.author}}\n",
    "config.ini": "[app]\nname = {{cookiecutter.project_slug}}\n",
    "settings/local.ini": "env = dev\n",
    "notes.txt": "v1 notes\n",
    "logs/error.log": "e1\n",
    "keep.log": "k1\n",
    ".cruftignore": CRUFTIGNORE_V1,
}

FILES_V2 = {
    "README.md": "# {{cookiecutter.project_slug}}\nby {{cookiecutter.author}}\nv2\n",
    "config.ini": "[app]\nname = {{cookiecutter.project_slug}}\ndebug = false\n",
    "settings/local.ini": "env = prod\n",
    "notes.txt": "v2 notes\n",
    "logs/error.log": "e2\n",
    "keep.log": "k2\n",
    "settings/extra.ini": "x = 1\n",
}

# Golden rendered contents used across many assertions.
README_V1 = "# webapp\nby Alice\n"
README_V2 = "# webapp\nby Alice\nv2\n"
CONFIG_V1 = "[app]\nname = webapp\n"
CONFIG_USER = "[app]\nname = webapp\n# local tweaks\n"
SETTINGS_USER = "env = staging\n"
NOTES_V1 = "v1 notes\n"
NOTES_V2 = "v2 notes\n"
ERROR_LOG_V1 = "e1\n"
KEEP_LOG_V2 = "k2\n"

# Golden expected skip lines for the fixture project on FILES_V1 -> FILES_V2,
# sorted alphabetically by POSIX-style project-relative path.
GOLDEN_SKIP_LINES = [
    "Skipping ignored path: config.ini",
    "Skipping ignored path: logs/error.log",
    "Skipping ignored path: settings/extra.ini",
    "Skipping ignored path: settings/local.ini",
]

# Exact regex the skip announcement must match (used to catch approximate
# reimplementations that emit extra decoration or use OS separators).
SKIP_LINE_RE = re.compile(r"^Skipping ignored path: (\S(?:.*\S)?)$")


def _git(args, cwd):
    env = dict(os.environ)
    env.update(GIT_ENV)
    subprocess.run(
        ["git"] + list(args),
        cwd=str(cwd),
        env=env,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _commit_all(repo_dir, message):
    _git(["add", "-A"], repo_dir)
    _git(["commit", "-qm", message, "--no-gpg-sign"], repo_dir)


def write_template(root, files, message):
    """Write (or extend) a local git cookiecutter template."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if not (root / "cookiecutter.json").exists():
        (root / "cookiecutter.json").write_text(json.dumps(CONTEXT))
        _git(["init", "-q", "-b", "main"], root)
    template_dir = root / "{{cookiecutter.project_slug}}"
    for relative, content in files.items():
        path = template_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    _commit_all(root, message)
    return root


def template_head(root):
    env = dict(os.environ)
    env.update(GIT_ENV)
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(root),
        env=env,
        check=True,
        stdout=subprocess.PIPE,
    )
    return out.stdout.decode().strip()


def run_cruft(args, cwd, stdin=None):
    env = dict(os.environ)
    env.update(GIT_ENV)
    return subprocess.run(
        [sys.executable, "-m", "cruft"] + list(args),
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        input=stdin,
        text=True,
    )


def cruft_create(template, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result = run_cruft(
        ["create", "--output-dir", str(output_dir), str(template), "-y"],
        cwd=output_dir,
    )
    assert result.returncode == 0, result.stdout
    return Path(output_dir) / "webapp"


def cruft_update(project, extra_args=()):
    return run_cruft(["update", "--project-dir", ".", "-y"] + list(extra_args), cwd=project)


def cruft_diff(project, extra_args=()):
    return run_cruft(["diff", "--project-dir", "."] + list(extra_args), cwd=project)


def read_state(project):
    return json.loads((Path(project) / ".cruft.json").read_text())


def rej_files(project):
    return sorted(p.as_posix() for p in Path(project).rglob("*.rej"))


def all_files(project):
    return sorted(
        p.relative_to(project).as_posix()
        for p in Path(project).rglob("*")
        if p.is_file()
    )


def skip_lines(output):
    """Extract the exact captured payload of each Skipping-line."""
    matches = []
    for line in output.splitlines():
        m = SKIP_LINE_RE.search(line)
        if m:
            matches.append(m.group(1).strip())
    return matches


@pytest.fixture()
def project(tmp_path):
    """A project created from the v1 template, with user edits."""
    template = write_template(tmp_path / "template", FILES_V1, "v1")
    proj = cruft_create(template, tmp_path / "out")
    (proj / "config.ini").write_text(CONFIG_USER)
    (proj / "settings" / "local.ini").write_text(SETTINGS_USER)
    return template, proj


# ---------------------------------------------------------------------
# cruft update — core semantics
# ---------------------------------------------------------------------


def test_update_preserves_ignored_and_updates_managed(project):
    template, proj = project
    write_template(template, FILES_V2, "v2")

    result = cruft_update(proj)
    assert result.returncode == 0, result.stdout

    # Ignored paths: user content wins, nothing created, nothing removed.
    assert (proj / "config.ini").read_text() == CONFIG_USER
    assert (proj / "settings" / "local.ini").read_text() == SETTINGS_USER
    assert (proj / "logs" / "error.log").read_text() == ERROR_LOG_V1
    assert not (proj / "settings" / "extra.ini").exists()

    # Managed paths follow the template, including re-included ones.
    assert (proj / "README.md").read_text() == README_V2
    assert (proj / "notes.txt").read_text() == NOTES_V2
    assert (proj / "keep.log").read_text() == KEEP_LOG_V2

    # No conflict leftovers anywhere.
    assert rej_files(proj) == []

    # The cruft state moved to the new template commit.
    assert read_state(proj)["commit"] == template_head(template)


def test_update_skip_lines_are_exact_and_sorted(project):
    """The skip announcement must match the golden lines verbatim, in the
    exact order (alphabetical, POSIX-style relative paths, no os.sep)."""
    template, proj = project
    write_template(template, FILES_V2, "v2")

    result = cruft_update(proj)
    assert result.returncode == 0, result.stdout

    reported_lines = [
        line.strip()
        for line in result.stdout.splitlines()
        if "Skipping ignored path:" in line
    ]
    # ANSI colour codes may wrap the text -- strip them before comparing.
    ansi = re.compile(r"\x1b\[[0-9;]*m")
    reported_lines = [ansi.sub("", line) for line in reported_lines]

    assert reported_lines == GOLDEN_SKIP_LINES

    # Paths embedded inside each line: POSIX-style, project relative,
    # alphabetically sorted.
    paths = skip_lines(result.stdout)
    assert paths == sorted(paths)
    assert all("\\" not in p for p in paths), paths
    assert all(not p.startswith("/") for p in paths), paths


def test_update_ignored_deleted_template_file_is_kept(project):
    template, proj = project
    (template / "{{cookiecutter.project_slug}}" / "logs" / "error.log").unlink()
    _commit_all(template, "v2 removes error.log")

    result = cruft_update(proj)
    assert result.returncode == 0, result.stdout
    assert (proj / "logs" / "error.log").read_text() == ERROR_LOG_V1
    assert rej_files(proj) == []


def test_update_managed_deleted_template_file_is_removed(project):
    template, proj = project
    (template / "{{cookiecutter.project_slug}}" / "notes.txt").unlink()
    _commit_all(template, "v2 removes notes.txt")

    result = cruft_update(proj)
    assert result.returncode == 0, result.stdout
    assert not (proj / "notes.txt").exists()


def test_update_ignored_new_template_file_is_not_created(project):
    template, proj = project
    write_template(
        template,
        {"settings/extra.ini": "x = 1\n", "README.md": README_V2},
        "v2",
    )

    result = cruft_update(proj)
    assert result.returncode == 0, result.stdout
    assert not (proj / "settings" / "extra.ini").exists()
    assert (proj / "README.md").read_text() == README_V2


def test_update_ignored_user_deleted_file_is_not_recreated(project):
    template, proj = project
    (proj / "config.ini").unlink()
    write_template(template, FILES_V2, "v2")

    result = cruft_update(proj)
    assert result.returncode == 0, result.stdout
    assert not (proj / "config.ini").exists()


def test_cruftignore_file_itself_is_updated(project):
    template, proj = project
    new_rules = "config.ini\nsettings/\n*.log\n!keep.log\ndocs/\n"
    write_template(template, {".cruftignore": new_rules}, "v2 new rules")

    result = cruft_update(proj)
    assert result.returncode == 0, result.stdout
    assert (proj / ".cruftignore").read_text() == new_rules


def test_cruftignore_is_never_self_ignored(tmp_path):
    """Even rules that syntactically match `.cruftignore` (like `*` or
    `.*`) must not cause the ignore file itself to become unmanaged."""
    files = dict(FILES_V1)
    files[".cruftignore"] = "*\n**\n.*\n"
    template = write_template(tmp_path / "template", files, "v1")
    proj = cruft_create(template, tmp_path / "out")
    assert (proj / ".cruftignore").read_text() == "*\n**\n.*\n"

    # Change every file including .cruftignore.
    write_template(
        template,
        {
            "README.md": "# {{cookiecutter.project_slug}}\nv2\n",
            "config.ini": "[app]\nv2\n",
            "notes.txt": "n2\n",
            ".cruftignore": "config.ini\n",
        },
        "v2",
    )

    result = cruft_update(proj)
    assert result.returncode == 0, result.stdout
    # .cruftignore updates like any managed file.
    assert (proj / ".cruftignore").read_text() == "config.ini\n"


def test_rules_come_from_new_template_version(tmp_path):
    """The rule set that applies during update is the *new* version's,
    even when the old version ignored a file the new one manages."""
    files = dict(FILES_V1)
    files[".cruftignore"] = "config.ini\n"
    template = write_template(tmp_path / "template", files, "v1")
    proj = cruft_create(template, tmp_path / "out")
    (proj / "config.ini").write_text(CONFIG_USER)

    # v2 unignores config.ini and updates it.
    write_template(
        template,
        {
            "config.ini": "[app]\nname = {{cookiecutter.project_slug}}\nv2\n",
            ".cruftignore": "settings/\n",
        },
        "v2",
    )

    result = cruft_update(proj)
    assert result.returncode == 0, result.stdout
    # Even though the user had local changes, the file is managed again;
    # a 3-way merge either keeps user content plus a .rej, or applies the
    # template on top -- either way the file is no longer skipped.
    assert "Skipping ignored path: config.ini" not in result.stdout


def test_project_only_cruftignore_has_no_effect(tmp_path):
    """A stray .cruftignore in the project (not shipped by the template)
    is just a user file -- the template controls the rule set."""
    files = {k: v for k, v in FILES_V1.items() if k != ".cruftignore"}
    template = write_template(tmp_path / "template", files, "v1")
    proj = cruft_create(template, tmp_path / "out")
    (proj / ".cruftignore").write_text("notes.txt\n")

    write_template(template, {"notes.txt": NOTES_V2}, "v2")
    result = cruft_update(proj)
    assert result.returncode == 0, result.stdout
    assert (proj / "notes.txt").read_text() == NOTES_V2
    assert (proj / ".cruftignore").read_text() == "notes.txt\n"  # user file untouched


def test_no_skip_report_when_ignored_paths_unchanged(project):
    """Ignored paths that the template didn't touch this update must not
    appear in the skip announcement."""
    template, proj = project
    write_template(template, {"README.md": README_V2}, "v2")

    result = cruft_update(proj)
    assert result.returncode == 0, result.stdout
    assert "Skipping ignored path:" not in result.stdout


def test_pattern_semantics_end_to_end(tmp_path):
    """Exercise gitignore pattern semantics: anchoring, directory-only,
    globstar, negation and last-rule-wins, via observable behavior."""
    files = {
        "rooted.txt": "root v1\n",
        "sub/rooted.txt": "sub v1\n",
        "build/out.txt": "built v1\n",
        "src/a/gen_x.py": "gen v1\n",
        "src/b/keep.py": "keep v1\n",
        "logs/keep.log": "kept v1\n",
        "logs/drop.log": "drop v1\n",
        # /rooted.txt      -> only the root file, not sub/rooted.txt
        # build/           -> whole directory ignored
        # src/**/gen_*.py  -> generated python files
        # *.log then !keep.log -> keep.log is re-included
        ".cruftignore": (
            "/rooted.txt\n"
            "build/\n"
            "src/**/gen_*.py\n"
            "*.log\n"
            "!keep.log\n"
        ),
    }
    template = write_template(tmp_path / "template", files, "v1")
    proj = cruft_create(template, tmp_path / "out")
    write_template(
        template,
        {
            "rooted.txt": "root v2\n",
            "sub/rooted.txt": "sub v2\n",
            "build/out.txt": "built v2\n",
            "build/new.txt": "brand new\n",
            "src/a/gen_x.py": "gen v2\n",
            "src/b/keep.py": "keep v2\n",
            "logs/keep.log": "kept v2\n",
            "logs/drop.log": "drop v2\n",
        },
        "v2",
    )

    result = cruft_update(proj)
    assert result.returncode == 0, result.stdout

    assert (proj / "rooted.txt").read_text() == "root v1\n"
    assert (proj / "sub" / "rooted.txt").read_text() == "sub v2\n"
    assert (proj / "build" / "out.txt").read_text() == "built v1\n"
    assert not (proj / "build" / "new.txt").exists()
    assert (proj / "src" / "a" / "gen_x.py").read_text() == "gen v1\n"
    assert (proj / "src" / "b" / "keep.py").read_text() == "keep v2\n"
    assert (proj / "logs" / "drop.log").read_text() == "drop v1\n"
    # Negated last-rule-wins: keep.log is managed even though *.log ignores it.
    assert (proj / "logs" / "keep.log").read_text() == "kept v2\n"


def test_update_creates_no_rej_files_in_git_repo(tmp_path):
    """Regression contract: not only in the ``--reject`` fallback path,
    but also inside a real git repository (where cruft uses 3-way merge)
    ignored paths must never produce ``.rej`` files."""
    template = write_template(tmp_path / "template", FILES_V1, "v1")
    proj = cruft_create(template, tmp_path / "out")

    # Turn the project into a git repo and commit user edits to
    # ignored files, so the 3-way merge code path is exercised.
    _git(["init", "-q", "-b", "main"], proj)
    (proj / "config.ini").write_text(CONFIG_USER)
    (proj / "settings" / "local.ini").write_text(SETTINGS_USER)
    _commit_all(proj, "project with local tweaks")

    write_template(template, FILES_V2, "v2")

    result = cruft_update(proj)
    assert result.returncode == 0, result.stdout

    # Ignored files still hold the user's content.
    assert (proj / "config.ini").read_text() == CONFIG_USER
    assert (proj / "settings" / "local.ini").read_text() == SETTINGS_USER
    # Managed content still moves forward.
    assert (proj / "README.md").read_text() == README_V2
    # No .rej files anywhere.
    assert rej_files(proj) == []


def test_update_composes_with_existing_skip_mechanism(tmp_path):
    """.cruftignore must layer on top of the pre-existing ``skip`` list
    from ``.cruft.json`` / ``pyproject.toml``, not replace it."""
    files = dict(FILES_V1)
    files[".cruftignore"] = "config.ini\n"
    files["extra_managed.txt"] = "template v1\n"
    template = write_template(tmp_path / "template", files, "v1")
    proj = cruft_create(template, tmp_path / "out")

    # Add a `skip` entry in .cruft.json so that extra_managed.txt is
    # protected by the OLD mechanism, while config.ini is protected by
    # the NEW one. Both must be respected.
    state = json.loads((proj / ".cruft.json").read_text())
    state["skip"] = ["extra_managed.txt"]
    (proj / ".cruft.json").write_text(json.dumps(state))

    (proj / "config.ini").write_text(CONFIG_USER)
    (proj / "extra_managed.txt").write_text("user edited\n")

    write_template(
        template,
        {
            "config.ini": "[app]\nname = {{cookiecutter.project_slug}}\nv2\n",
            "extra_managed.txt": "template v2\n",
            "notes.txt": NOTES_V2,
        },
        "v2",
    )

    result = cruft_update(proj)
    assert result.returncode == 0, result.stdout

    # Both mechanisms preserved their users' content.
    assert (proj / "config.ini").read_text() == CONFIG_USER
    assert (proj / "extra_managed.txt").read_text() == "user edited\n"
    # A managed file still updated normally.
    assert (proj / "notes.txt").read_text() == NOTES_V2
    assert rej_files(proj) == []


def test_update_with_skip_update_flag_still_bumps_state(project):
    template, proj = project
    write_template(template, FILES_V2, "v2")

    result = cruft_update(proj, ["--skip-update"])
    assert result.returncode == 0, result.stdout

    # skip_update: no files applied; state still moves forward.
    assert read_state(proj)["commit"] == template_head(template)
    assert (proj / "notes.txt").read_text() == NOTES_V1
    assert (proj / "config.ini").read_text() == CONFIG_USER


def test_update_with_checkout_uses_that_versions_rules(tmp_path):
    """When updating to a specific commit via --checkout, that commit's
    .cruftignore is what governs the update."""
    files = dict(FILES_V1)
    files[".cruftignore"] = "config.ini\n"
    template = write_template(tmp_path / "template", files, "v1")
    proj = cruft_create(template, tmp_path / "out")
    (proj / "config.ini").write_text(CONFIG_USER)

    # v2 updates config.ini AND keeps the ignore rule.
    write_template(
        template,
        {"config.ini": "[app]\nname = {{cookiecutter.project_slug}}\nv2\n"},
        "v2",
    )
    v2 = template_head(template)
    # v3 removes the ignore rule.
    write_template(template, {".cruftignore": ""}, "v3 unignore")

    result = cruft_update(proj, ["--checkout", v2, "--not-strict"])
    assert result.returncode == 0, result.stdout
    # v2's rules ignored config.ini, so the user's content is preserved.
    assert (proj / "config.ini").read_text() == CONFIG_USER
    assert read_state(proj)["commit"] == v2


# ---------------------------------------------------------------------
# cruft diff — semantics
# ---------------------------------------------------------------------


def test_diff_excludes_ignored_paths(project):
    template, proj = project
    (proj / "notes.txt").write_text("user notes\n")

    result = cruft_diff(proj)
    assert result.returncode == 0, result.stdout
    assert "notes.txt" in result.stdout
    for line in result.stdout.splitlines():
        if line.startswith(("--- ", "+++ ", "diff --git")):
            assert "config.ini" not in line, line
            assert "settings" not in line, line
            assert "error.log" not in line, line


def test_diff_exit_code_counts_only_managed_differences(project):
    template, proj = project

    # Only ignored paths differ so far (user edits from the fixture).
    result = cruft_diff(proj, ["--exit-code"])
    assert result.returncode == 0, result.stdout

    (proj / "notes.txt").write_text("user notes\n")
    result = cruft_diff(proj, ["--exit-code"])
    assert result.returncode == 1, result.stdout


def test_diff_output_is_clean_patch_text(project):
    template, proj = project
    (proj / "notes.txt").write_text("user notes\n")

    result = cruft_diff(proj)
    assert result.returncode == 0, result.stdout
    # No advisory chatter mixed into the patch output.
    assert "Skipping" not in result.stdout
    assert "ignored" not in result.stdout.lower()


def test_diff_output_is_a_valid_patch(project, tmp_path):
    """The captured diff must be parseable as a git patch and apply
    cleanly on a replica of the project."""
    template, proj = project
    (proj / "notes.txt").write_text("user notes\n")

    result = cruft_diff(proj)
    assert result.returncode == 0, result.stdout
    patch_text = result.stdout
    assert patch_text.strip() != ""

    # Sanity: only `notes.txt` should be in there.
    assert "notes.txt" in patch_text
    assert "config.ini" not in patch_text

    # Apply the patch to a replica; the tool used is deliberately GNU
    # patch (not git) to avoid needing a git repo for the target.
    replica = tmp_path / "replica"
    shutil.copytree(proj, replica)
    normalized = patch_text.replace("upstream-template-old/", "").replace(
        "upstream-template-new/", ""
    )
    apply = subprocess.run(
        ["patch", "-p0", "--batch", "--forward"],
        input=normalized,
        cwd=str(replica),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert apply.returncode == 0, apply.stderr
    # After applying the diff notes.txt is reset to the template content.
    assert (replica / "notes.txt").read_text() == NOTES_V1


def test_diff_after_successful_update_is_clean(project):
    """Once ``cruft update`` has run, running ``cruft diff`` with only
    the ignored-path drift should return 0 and produce no patch text."""
    template, proj = project
    write_template(template, FILES_V2, "v2")

    up = cruft_update(proj)
    assert up.returncode == 0, up.stdout

    result = cruft_diff(proj, ["--exit-code"])
    assert result.returncode == 0, result.stdout


def test_diff_uses_checkout_versions_rules(tmp_path):
    """The rule set governing ``cruft diff`` is the checkout version's,
    not the latest one on the branch."""
    files = dict(FILES_V1)
    files[".cruftignore"] = "config.ini\n"
    template = write_template(tmp_path / "template", files, "v1")
    v1 = template_head(template)
    proj = cruft_create(template, tmp_path / "out")

    # Later version drops the rule.
    write_template(template, {".cruftignore": ""}, "v2 empty rules")

    # User has a local edit.
    (proj / "config.ini").write_text(CONFIG_USER)

    # Diff against the *original* commit uses the original rules --
    # config.ini is ignored, exit-code is 0.
    result = cruft_diff(proj, ["--exit-code", "--checkout", v1])
    assert result.returncode == 0, result.stdout


# ---------------------------------------------------------------------
# cruft create / check — regression contracts
# ---------------------------------------------------------------------


def test_create_renders_everything(tmp_path):
    template = write_template(tmp_path / "template", FILES_V1, "v1")
    proj = cruft_create(template, tmp_path / "out")

    assert (proj / ".cruftignore").read_text() == CRUFTIGNORE_V1
    assert (proj / "README.md").read_text() == README_V1
    assert (proj / "config.ini").read_text() == CONFIG_V1
    assert (proj / "logs" / "error.log").read_text() == ERROR_LOG_V1
    assert (proj / "settings" / "local.ini").read_text() == "env = dev\n"


def test_create_renders_everything_even_with_wildcard_rules(tmp_path):
    """Even a wildcard rule set must not stop ``cruft create`` from
    producing the starter tree."""
    files = dict(FILES_V1)
    files[".cruftignore"] = "*\n**\n"
    template = write_template(tmp_path / "template", files, "v1")
    proj = cruft_create(template, tmp_path / "out")

    for relative in FILES_V1:
        assert (proj / relative).exists(), relative


def test_check_unaffected(project):
    """``cruft check`` behavior is unchanged: it still compares commit
    hashes, and reports the project as out of date whenever the template
    has any new commits."""
    template, proj = project
    result = run_cruft(["check", "--project-dir", "."], cwd=proj)
    assert result.returncode == 0, result.stdout

    write_template(template, FILES_V2, "v2")
    result = run_cruft(["check", "--project-dir", "."], cwd=proj)
    assert result.returncode == 1, result.stdout


# ---------------------------------------------------------------------
# Existing offline unit tests must keep passing after the change
# ---------------------------------------------------------------------


def test_existing_offline_unit_tests_still_pass():
    """Sanity: pre-existing pytest units shipped with cruft (the ones
    that don't need the network) still all pass after the change."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
            f"{APP_DIR}/tests/test_utils.py",
            f"{APP_DIR}/tests/test_exceptions.py",
            "-q",
            "-k",
            "not test_remove_paths_with_glob_pattern_and_string",
        ],
        cwd=APP_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert result.returncode == 0, result.stdout


def test_utils_generate_remove_paths_unchanged_regression():
    """The legacy ``_remove_paths`` helper must still accept a plain
    set of ``Path`` objects and remove them, without knowing anything
    about gitignore semantics. If this fails, the ``.cruftignore``
    integration accidentally broke the pre-existing skip mechanism."""
    import tempfile
    from cruft._commands import utils as cruft_utils

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "a").mkdir()
        (root / "a" / "b.txt").write_text("bye")
        (root / "keep.txt").write_text("hi")
        cruft_utils.generate._remove_paths(root, {Path("a/b.txt")})
        assert not (root / "a" / "b.txt").exists()
        assert (root / "keep.txt").exists()
