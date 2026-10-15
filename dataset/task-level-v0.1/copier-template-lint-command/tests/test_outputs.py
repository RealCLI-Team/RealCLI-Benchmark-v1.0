"""Behavioral checks for the `copier lint` template-validation feature.

The checks exercise only the public command line interface: frozen golden
templates are built in temporary directories, `copier lint` is executed as a
subprocess, and its exit code and stdout/stderr are compared against frozen
expectations embedded in this file. Everything runs offline.

The suite deliberately covers three orthogonal concerns:

- **surface behaviour** — that plain and JSON output match the documented
  format, exit codes are the ones the spec asks for, findings are ordered
  deterministically, and rule codes are stable identifiers;
- **edge cases** — templated values must be skipped, multi-document YAML
  and YAML with `!include` tags must be accepted, `_envops` custom
  delimiters must be respected, and `_templates_suffix` must be honoured
  (including the empty-string special case that renders every file);
- **regression / parity contracts** — templates the runtime accepts must
  not produce lint errors, templates the runtime rejects must produce a
  lint error with a matching rule code, and previously working commands
  (`copier copy`, `copier update`, `--version`, `--help`) must keep
  behaving the same after the linter is added.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ANSWERS_FILE_TEMPLATE = (
    '# Changes here will be overwritten by Copier\n'
    '{{ _copier_answers|to_nice_yaml }}\n'
)

VALID_CONFIG = (
    'project_name:\n'
    '    type: str\n'
    '    help: Your project name\n'
    '    default: my-project\n'
    '\n'
    'license:\n'
    '    type: str\n'
    '    default: MIT\n'
    '    choices:\n'
    '        - MIT\n'
    '        - Apache-2.0\n'
    '\n'
    'use_docker:\n'
    '    type: bool\n'
    '    default: false\n'
    "    when: \"{{ license == 'MIT' }}\"\n"
)


def write_template(root, config, files=None, config_name='copier.yml'):
    """Write a frozen template below ``root`` and return its path."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / config_name).write_text(config, encoding='utf-8')
    for relpath, content in (files or {}).items():
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    return root


def make_valid_template(tmp_path):
    """A template without any findings."""
    return write_template(
        tmp_path / 'valid',
        VALID_CONFIG,
        {
            '{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE,
            'README.md.jinja': '# {{ project_name }}\n',
        },
    )


def run_lint(*args, cwd=None):
    """Run ``copier lint`` as a subprocess and return the CompletedProcess."""
    return subprocess.run(
        [sys.executable, '-m', 'copier', 'lint', *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )


def lint_json(*args):
    """Run ``copier lint --format json`` and return (exit_code, payload)."""
    result = run_lint(*args, '--format', 'json')
    return result.returncode, json.loads(result.stdout)


def summary_line(stdout):
    """Extract the final summary line from plain output."""
    return stdout.strip().splitlines()[-1]


def find_by_code(findings, code):
    """Return every finding matching a rule code."""
    return [finding for finding in findings if finding['code'] == code]


class TestCleanTemplate:
    """A well-formed template produces no findings and exits 0."""

    def test_plain_output(self, tmp_path):
        template = make_valid_template(tmp_path)
        result = run_lint(str(template))
        assert result.returncode == 0, result.stderr
        assert summary_line(result.stdout) == '0 error(s) and 0 warning(s) found'
        assert len(result.stdout.strip().splitlines()) == 1

    def test_json_output(self, tmp_path):
        template = make_valid_template(tmp_path)
        code, payload = lint_json(str(template))
        assert code == 0
        assert payload == {'findings': [], 'errors': 0, 'warnings': 0}

    def test_default_path_is_cwd(self, tmp_path):
        template = make_valid_template(tmp_path)
        result = run_lint(cwd=template)
        assert result.returncode == 0, result.stderr

    def test_copier_yaml_extension_accepted(self, tmp_path):
        template = write_template(
            tmp_path / 'yaml-ext',
            'project_name: my-project\n',
            {'{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE},
            config_name='copier.yaml',
        )
        result = run_lint(str(template))
        assert result.returncode == 0, result.stderr

    def test_multi_document_yaml_accepted(self, tmp_path):
        template = write_template(
            tmp_path / 'multi-doc',
            '---\nproject_name:\n  type: str\n  default: demo\n'
            '---\n_exclude:\n  - "*.tmp"\n',
            {'{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE},
        )
        result = run_lint(str(template))
        assert result.returncode == 0, result.stderr


class TestUsageErrors:
    """Wrong invocations exit 2 and never print a report."""

    def test_missing_path(self, tmp_path):
        result = run_lint(str(tmp_path / 'does-not-exist'))
        assert result.returncode == 2
        assert result.stderr.strip()

    def test_path_is_a_file(self, tmp_path):
        some_file = tmp_path / 'file.txt'
        some_file.write_text('hello', encoding='utf-8')
        result = run_lint(str(some_file))
        assert result.returncode == 2

    def test_remote_url_rejected(self):
        result = run_lint('gh:copier-org/some-template')
        assert result.returncode == 2

    def test_https_url_rejected(self):
        result = run_lint('https://github.com/copier-org/copier.git')
        assert result.returncode == 2

    def test_git_at_url_rejected(self):
        result = run_lint('git@github.com:copier-org/copier.git')
        assert result.returncode == 2

    def test_usage_error_prints_no_summary(self, tmp_path):
        result = run_lint(str(tmp_path / 'nope'))
        assert 'warning(s) found' not in result.stdout
        assert 'error(s)' not in result.stdout


class TestConfigFileFindings:
    """Findings about the configuration file itself."""

    def test_config_not_found(self, tmp_path):
        empty = tmp_path / 'empty'
        empty.mkdir()
        code, payload = lint_json(str(empty))
        assert code == 1
        assert payload['errors'] == 1
        assert payload['findings'][0]['code'] == 'config-not-found'
        assert payload['findings'][0]['severity'] == 'error'
        assert payload['findings'][0]['file'] is None
        assert payload['findings'][0]['line'] is None

    def test_multiple_config_files(self, tmp_path):
        template = tmp_path / 'multi'
        template.mkdir()
        (template / 'copier.yml').write_text('foo: bar\n', encoding='utf-8')
        (template / 'copier.yaml').write_text('foo: bar\n', encoding='utf-8')
        code, payload = lint_json(str(template))
        assert code == 1
        assert [f['code'] for f in payload['findings']] == ['multiple-config-files']

    def test_invalid_yaml_with_line_number(self, tmp_path):
        template = write_template(
            tmp_path / 'bad-yaml', 'foo: bar\n  baz: [\n'
        )
        code, payload = lint_json(str(template))
        assert code == 1
        (finding,) = payload['findings']
        assert finding['code'] == 'config-invalid-yaml'
        assert finding['file'] == 'copier.yml'
        assert finding['line'] == 2

    def test_config_not_mapping(self, tmp_path):
        template = write_template(tmp_path / 'not-mapping', '- just\n- a\n- list\n')
        code, payload = lint_json(str(template))
        assert code == 1
        assert [f['code'] for f in payload['findings']] == ['config-not-mapping']

    def test_config_file_stops_further_analysis(self, tmp_path):
        empty = tmp_path / 'empty'
        empty.mkdir()
        code, payload = lint_json(str(empty))
        assert code == 1
        # A missing config file must be the only finding — the walker must
        # not proceed to scan for template files or answers-file templates
        # when no configuration exists.
        assert len(payload['findings']) == 1


class TestQuestionFindings:
    """Findings about question definitions, with frozen expectations."""

    CONFIG = (
        'bad_type:\n'
        '    type: uuid\n'
        'bad_cast:\n'
        '    type: int\n'
        '    default: not-a-number\n'
        'null_default:\n'
        '    type: str\n'
        '    default: null\n'
        'not_a_choice:\n'
        '    type: str\n'
        '    default: gamma\n'
        '    choices: [alpha, beta]\n'
        'multi_no_choices:\n'
        '    type: str\n'
        '    multiselect: true\n'
        'bad_choices:\n'
        '    type: str\n'
        '    choices: 42\n'
        'secret_no_default:\n'
        '    type: str\n'
        '    secret: true\n'
        'bad_when:\n'
        "    when: '{{ unclosed'\n"
        'bad_validator:\n'
        '    validator: true\n'
        'now: yesterday\n'
        'typo_key:\n'
        '    type: str\n'
        '    unknwon: oops\n'
        'bad_help:\n'
        '    type: str\n'
        '    help: [a, b]\n'
    )

    EXPECTED_CODES = {
        'bad_type': 'invalid-question-type',
        'bad_cast': 'invalid-default',
        'null_default': 'invalid-default',
        'not_a_choice': 'default-not-in-choices',
        'multi_no_choices': 'multiselect-without-choices',
        'bad_choices': 'invalid-choices',
        'secret_no_default': 'secret-without-default',
        'bad_when': 'invalid-when',
        'bad_validator': 'invalid-validator',
        'now': 'reserved-question-name',
    }

    def test_error_codes(self, tmp_path):
        template = write_template(tmp_path / 'questions', self.CONFIG)
        code, payload = lint_json(str(template))
        assert code == 1
        findings = payload['findings']
        for name, expected_code in self.EXPECTED_CODES.items():
            matches = [
                f for f in findings
                if f['code'] == expected_code and name in f['message']
            ]
            assert matches, f'no {expected_code} finding mentioning {name}'
            assert all(f['severity'] == 'error' for f in matches)
            assert all(f['file'] == 'copier.yml' for f in matches)

    def test_warning_codes(self, tmp_path):
        template = write_template(tmp_path / 'questions', self.CONFIG)
        _, payload = lint_json(str(template))
        warnings = [f for f in payload['findings'] if f['severity'] == 'warning']
        codes = sorted(f['code'] for f in warnings)
        assert codes == [
            'answers-file-missing',
            'invalid-help',
            'unknown-question-key',
        ]

    def test_counts(self, tmp_path):
        template = write_template(tmp_path / 'questions', self.CONFIG)
        code, payload = lint_json(str(template))
        assert code == 1
        assert payload['errors'] == 10
        assert payload['warnings'] == 3

    def test_finding_schema(self, tmp_path):
        template = write_template(tmp_path / 'questions', self.CONFIG)
        _, payload = lint_json(str(template))
        for finding in payload['findings']:
            assert set(finding) == {'severity', 'code', 'file', 'line', 'message'}
            assert finding['severity'] in {'error', 'warning'}

    def test_templated_default_is_not_checked(self, tmp_path):
        template = write_template(
            tmp_path / 'templated',
            'flag:\n'
            '    type: int\n'
            "    default: '{{ 1 + 1 }}'\n",
            {'{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE},
        )
        code, payload = lint_json(str(template))
        # A templated default is only known at render time — no lint error.
        assert code == 0
        assert payload['errors'] == 0

    def test_templated_choices_are_not_checked(self, tmp_path):
        template = write_template(
            tmp_path / 'templated-choices',
            'kind:\n'
            '    type: str\n'
            "    choices: '{{ [\"a\", \"b\"] }}'\n"
            '    default: whatever\n',
            {'{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE},
        )
        code, payload = lint_json(str(template))
        assert code == 0
        assert not find_by_code(payload['findings'], 'default-not-in-choices')

    def test_multiselect_default_subset(self, tmp_path):
        template = write_template(
            tmp_path / 'multi-ok',
            'flavors:\n'
            '    type: str\n'
            '    multiselect: true\n'
            '    choices: [a, b, c]\n'
            '    default: [a, c]\n',
            {'{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE},
        )
        code, payload = lint_json(str(template))
        assert code == 0
        assert not find_by_code(payload['findings'], 'default-not-in-choices')

    def test_multiselect_default_not_subset(self, tmp_path):
        template = write_template(
            tmp_path / 'multi-bad',
            'flavors:\n'
            '    type: str\n'
            '    multiselect: true\n'
            '    choices: [a, b, c]\n'
            '    default: [a, z]\n',
            {'{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE},
        )
        code, payload = lint_json(str(template))
        assert code == 1
        assert find_by_code(payload['findings'], 'default-not-in-choices')

    def test_yaml_type_allows_null_default(self, tmp_path):
        template = write_template(
            tmp_path / 'yaml-null',
            'data:\n    type: yaml\n    default: null\n',
            {'{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE},
        )
        code, payload = lint_json(str(template))
        assert code == 0
        assert not find_by_code(payload['findings'], 'invalid-default')

    def test_json_type_allows_null_default(self, tmp_path):
        template = write_template(
            tmp_path / 'json-null',
            'data:\n    type: json\n    default: null\n',
            {'{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE},
        )
        code, payload = lint_json(str(template))
        assert code == 0

    def test_short_form_question_is_valid(self, tmp_path):
        template = write_template(
            tmp_path / 'short',
            'greeting: hello\n',
            {'{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE},
        )
        code, payload = lint_json(str(template))
        assert code == 0

    def test_make_secret_conflict_is_error(self, tmp_path):
        template = write_template(
            tmp_path / 'make-secret',
            'make_secret: overwrite\n',
            {'{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE},
        )
        code, payload = lint_json(str(template))
        assert code == 1
        matches = find_by_code(payload['findings'], 'reserved-question-name')
        assert matches
        assert 'make_secret' in matches[0]['message']

    def test_valid_when_expression_not_flagged(self, tmp_path):
        template = write_template(
            tmp_path / 'valid-when',
            'ok:\n'
            '    type: bool\n'
            '    default: false\n'
            "    when: '{{ true }}'\n",
            {'{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE},
        )
        code, payload = lint_json(str(template))
        assert code == 0
        assert not find_by_code(payload['findings'], 'invalid-when')


class TestSettingFindings:
    """Findings about underscore-prefixed settings."""

    def test_invalid_setting_shapes(self, tmp_path):
        template = write_template(
            tmp_path / 'bad-settings',
            'question: answer\n'
            '_exclude: not-a-list\n'
            '_envops: just-a-string\n'
            '_templates_suffix: false\n'
            '_tasks: single-string\n'
            '_migrations: {not: a-list}\n',
        )
        code, payload = lint_json(str(template))
        assert code == 1
        codes = [f['code'] for f in payload['findings'] if f['severity'] == 'error']
        assert codes.count('invalid-setting') == 5

    def test_min_copier_version_not_pep440(self, tmp_path):
        template = write_template(
            tmp_path / 'bad-version',
            'question: answer\n_min_copier_version: not-a-version\n',
        )
        code, payload = lint_json(str(template))
        assert code == 1
        assert 'invalid-setting' in {f['code'] for f in payload['findings']}

    def test_min_copier_version_too_new_is_warning(self, tmp_path):
        template = write_template(
            tmp_path / 'too-new',
            'question: answer\n_min_copier_version: \'999.0\'\n',
            {'{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE},
        )
        code, payload = lint_json(str(template))
        assert code == 0
        assert payload['errors'] == 0
        assert [f['code'] for f in payload['findings']] == [
            'min-copier-version-too-new'
        ]

    def test_envops_unknown_key(self, tmp_path):
        template = write_template(
            tmp_path / 'bad-envops',
            'question: answer\n_envops:\n  bogus_option: true\n',
        )
        code, payload = lint_json(str(template))
        assert code == 1
        assert 'invalid-setting' in {f['code'] for f in payload['findings']}

    def test_envops_bad_undefined(self, tmp_path):
        template = write_template(
            tmp_path / 'bad-undefined',
            'question: answer\n_envops:\n  undefined: my.custom.Undefined\n',
        )
        code, payload = lint_json(str(template))
        assert code == 1
        assert 'invalid-setting' in {f['code'] for f in payload['findings']}

    def test_jinja_extension_not_installed_is_warning(self, tmp_path):
        template = write_template(
            tmp_path / 'missing-ext',
            'question: answer\n'
            '_jinja_extensions:\n'
            '  - totally.bogus.ExtensionClass\n',
        )
        code, payload = lint_json(str(template))
        assert code == 0
        assert payload['errors'] == 0
        assert 'jinja-extension-not-installed' in {
            f['code'] for f in payload['findings']
        }

    def test_answers_file_must_be_relative(self, tmp_path):
        template = write_template(
            tmp_path / 'abs-answers',
            'question: answer\n_answers_file: /tmp/answers.yml\n',
        )
        code, payload = lint_json(str(template))
        assert code == 1
        assert 'invalid-setting' in {f['code'] for f in payload['findings']}

    def test_unknown_setting_is_warning(self, tmp_path):
        template = write_template(
            tmp_path / 'unknown-setting',
            'question: answer\n_weird_setting: 1\n',
        )
        code, payload = lint_json(str(template))
        assert code == 0
        assert 'unknown-setting' in {f['code'] for f in payload['findings']}

    def test_min_copier_version_current_ok(self, tmp_path):
        template = write_template(
            tmp_path / 'current-version',
            'question: answer\n_min_copier_version: \'1.0\'\n',
            {'{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE},
        )
        code, payload = lint_json(str(template))
        # A minimum version older than the running copier is not a problem.
        assert code == 0
        assert not find_by_code(
            payload['findings'], 'min-copier-version-too-new'
        )

    def test_tasks_dict_with_command_is_ok(self, tmp_path):
        template = write_template(
            tmp_path / 'tasks-ok',
            'question: answer\n'
            '_tasks:\n'
            '  - echo hi\n'
            '  - command: ["ls", "-la"]\n',
            {'{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE},
        )
        code, payload = lint_json(str(template))
        assert code == 0
        assert not find_by_code(payload['findings'], 'invalid-setting')

    def test_migrations_full_schema(self, tmp_path):
        template = write_template(
            tmp_path / 'migrations-ok',
            'question: answer\n'
            '_migrations:\n'
            '  - version: "1.0.0"\n'
            '    before: [echo, hi]\n'
            '    command: ls\n'
            '    after: pwd\n'
            '    when: "{{ true }}"\n'
            '    working_directory: subdir\n',
            {'{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE},
        )
        code, payload = lint_json(str(template))
        assert code == 0
        assert not find_by_code(payload['findings'], 'invalid-setting')

    def test_migration_bad_version_is_error(self, tmp_path):
        template = write_template(
            tmp_path / 'migration-bad',
            'question: answer\n'
            '_migrations:\n'
            '  - version: "not a version"\n'
            '    command: ls\n',
        )
        code, payload = lint_json(str(template))
        assert code == 1
        assert 'invalid-setting' in {f['code'] for f in payload['findings']}


class TestTemplateFileFindings:
    """Findings about the template files themselves."""

    def test_jinja_syntax_error_with_line(self, tmp_path):
        template = write_template(
            tmp_path / 'bad-jinja',
            'question: answer\n',
            {
                '{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE,
                'broken.md.jinja': 'line one\n{{ unclosed\n',
            },
        )
        code, payload = lint_json(str(template))
        assert code == 1
        (finding,) = payload['findings']
        assert finding['code'] == 'jinja-syntax-error'
        assert finding['severity'] == 'error'
        assert finding['file'] == 'broken.md.jinja'
        assert finding['line'] == 2

    def test_templated_path_syntax_error(self, tmp_path):
        template = write_template(
            tmp_path / 'bad-path',
            'question: answer\n',
            {
                '{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE,
                '{{ broken.txt': 'content\n',
            },
        )
        code, payload = lint_json(str(template))
        assert code == 1
        codes = {f['code'] for f in payload['findings']}
        assert 'templated-path-syntax-error' in codes

    def test_answers_file_missing_is_warning(self, tmp_path):
        template = write_template(
            tmp_path / 'no-answers',
            'question: answer\n',
            {'README.md.jinja': 'hello\n'},
        )
        code, payload = lint_json(str(template))
        assert code == 0
        assert [f['code'] for f in payload['findings']] == ['answers-file-missing']
        assert payload['findings'][0]['file'] is None
        assert payload['findings'][0]['line'] is None

    def test_excluded_files_are_skipped(self, tmp_path):
        template = write_template(
            tmp_path / 'excluded',
            'question: answer\n_exclude:\n  - broken/\n',
            {
                '{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE,
                'broken/file.md.jinja': '{{ oops\n',
            },
        )
        result = run_lint(str(template))
        assert result.returncode == 0, result.stderr

    def test_subdirectory_is_respected(self, tmp_path):
        template = write_template(
            tmp_path / 'subdir',
            'question: answer\n_subdirectory: tpl\n',
            {
                'tpl/{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE,
                'tpl/broken.md.jinja': '{{ oops\n',
                'outside.md.jinja': '{{ not scanned\n',
            },
        )
        code, payload = lint_json(str(template))
        assert code == 1
        files = {f['file'] for f in payload['findings']}
        assert 'broken.md.jinja' in files
        assert 'outside.md.jinja' not in files

    def test_subdirectory_not_found(self, tmp_path):
        template = write_template(
            tmp_path / 'no-subdir',
            'question: answer\n_subdirectory: missing\n',
        )
        code, payload = lint_json(str(template))
        assert code == 1
        assert 'subdirectory-not-found' in {f['code'] for f in payload['findings']}

    def test_custom_templates_suffix(self, tmp_path):
        template = write_template(
            tmp_path / 'custom-suffix',
            'question: answer\n_templates_suffix: .tpl\n',
            {
                '{{ _copier_conf.answers_file }}.tpl': ANSWERS_FILE_TEMPLATE,
                'broken.tpl': '{{ oops\n',
                'untouched.md.jinja': '{{ not parsed anymore\n',
            },
        )
        code, payload = lint_json(str(template))
        assert code == 1
        assert [f['file'] for f in payload['findings']] == ['broken.tpl']

    def test_empty_templates_suffix_parses_all_files(self, tmp_path):
        template = write_template(
            tmp_path / 'empty-suffix',
            "question: answer\n_templates_suffix: ''\n",
            {
                '{{ _copier_conf.answers_file }}': ANSWERS_FILE_TEMPLATE,
                'README.md': 'plain content\n',
                'broken.md': '{{ never closes\n',
            },
        )
        code, payload = lint_json(str(template))
        assert code == 1
        files = {f['file'] for f in payload['findings']}
        assert 'broken.md' in files

    def test_git_directory_is_skipped(self, tmp_path):
        template = write_template(
            tmp_path / 'has-git',
            'question: answer\n',
            {
                '{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE,
                '.git/broken.md.jinja': '{{ oops\n',
                '.hg/also-broken.md.jinja': '{{ oops\n',
                '.svn/subversion-bad.md.jinja': '{{ oops\n',
            },
        )
        code, payload = lint_json(str(template))
        assert code == 0
        assert not find_by_code(payload['findings'], 'jinja-syntax-error')

    def test_binary_file_is_skipped(self, tmp_path):
        template = tmp_path / 'binary'
        template.mkdir()
        (template / 'copier.yml').write_text('question: answer\n', encoding='utf-8')
        (template / '{{ _copier_conf.answers_file }}.jinja').write_text(
            ANSWERS_FILE_TEMPLATE, encoding='utf-8'
        )
        # Bytes with a NUL and invalid UTF-8 shape.
        (template / 'image.png.jinja').write_bytes(b'\x89PNG\r\n\x1a\n\x00\x00\x00')
        code, payload = lint_json(str(template))
        assert code == 0
        assert not find_by_code(payload['findings'], 'jinja-syntax-error')

    def test_envops_custom_delimiters_used_for_parsing(self, tmp_path):
        # Custom variable_start_string means the default `{{` is no longer a
        # Jinja marker. The template below is syntactically valid ONLY when
        # the linter respects the custom delimiters.
        template = write_template(
            tmp_path / 'custom-delim',
            'question: answer\n'
            "_envops:\n"
            "  variable_start_string: '[['\n"
            "  variable_end_string: ']]'\n",
            {
                '{{ _copier_conf.answers_file }}.jinja': (
                    '{{ this is not jinja anymore }}\n'
                ),
                'ok.md.jinja': 'plain [[ project_name ]] content\n',
            },
        )
        code, payload = lint_json(str(template))
        assert code == 0
        assert not find_by_code(payload['findings'], 'jinja-syntax-error')

    def test_answers_file_missing_reports_relative_form(self, tmp_path):
        template = write_template(
            tmp_path / 'no-answers-jinja',
            'question: answer\n',
            {'README.md.jinja': 'hi\n'},
        )
        _, payload = lint_json(str(template))
        matches = find_by_code(payload['findings'], 'answers-file-missing')
        assert matches
        assert '_copier_conf.answers_file' in matches[0]['message']


class TestPlainFormat:
    """The plain output format follows its documented shape exactly."""

    CONFIG = 'question:\n  type: uuid\nother: answer\n'

    FILES = {'broken.md.jinja': '{{ oops\n'}

    def test_line_shapes_and_summary(self, tmp_path):
        template = write_template(tmp_path / 'dirty', self.CONFIG, self.FILES)
        result = run_lint(str(template))
        assert result.returncode == 1
        lines = result.stdout.strip().splitlines()
        assert len(lines) == 4
        assert lines[0].startswith('broken.md.jinja:1: error [jinja-syntax-error] ')
        assert lines[1].startswith('copier.yml: error [invalid-question-type] ')
        assert lines[2].startswith('warning [answers-file-missing] ')
        assert lines[3] == '2 error(s) and 1 warning(s) found'

    def test_json_matches_plain_order(self, tmp_path):
        template = write_template(tmp_path / 'dirty', self.CONFIG, self.FILES)
        _, payload = lint_json(str(template))
        assert [f['code'] for f in payload['findings']] == [
            'jinja-syntax-error',
            'invalid-question-type',
            'answers-file-missing',
        ]
        assert payload['errors'] == 2
        assert payload['warnings'] == 1

    def test_empty_report_has_only_summary(self, tmp_path):
        template = write_template(
            tmp_path / 'clean',
            'question: answer\n',
            {'{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE},
        )
        result = run_lint(str(template))
        assert result.returncode == 0
        assert result.stdout.strip() == '0 error(s) and 0 warning(s) found'


class TestWarningsOnlyExitZero:
    """A template with only warnings exits 0."""

    def test_exit_code_and_counts(self, tmp_path):
        template = write_template(
            tmp_path / 'warnings-only',
            'question:\n  type: str\n  help: [oops]\n_weird_setting: 1\n',
        )
        code, payload = lint_json(str(template))
        assert code == 0
        assert payload['errors'] == 0
        assert payload['warnings'] == 3
        result = run_lint(str(template))
        assert result.returncode == 0
        assert summary_line(result.stdout) == '0 error(s) and 3 warning(s) found'


class TestLintIsReadOnly:
    """Linting never modifies the template it inspects."""

    def test_template_is_untouched(self, tmp_path):
        template = write_template(
            tmp_path / 'readonly',
            'question:\n  type: uuid\n',
            {'broken.md.jinja': '{{ oops\n'},
        )
        before = {
            str(p.relative_to(template)): p.read_bytes()
            for p in sorted(template.rglob('*'))
            if p.is_file()
        }
        result = run_lint(str(template))
        assert result.returncode == 1
        after = {
            str(p.relative_to(template)): p.read_bytes()
            for p in sorted(template.rglob('*'))
            if p.is_file()
        }
        assert before == after

    def test_lint_creates_no_new_paths(self, tmp_path):
        template = write_template(
            tmp_path / 'no-side-effects',
            'question: answer\n',
            {'{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE},
        )
        before = {str(p.relative_to(template)) for p in template.rglob('*')}
        run_lint(str(template))
        after = {str(p.relative_to(template)) for p in template.rglob('*')}
        assert before == after


class TestJsonSerialization:
    """The JSON format is stable and complete."""

    def test_all_fields_present_even_when_null(self, tmp_path):
        template = write_template(
            tmp_path / 'nulls',
            'question: answer\n',
            {'README.md.jinja': 'hi\n'},
        )
        _, payload = lint_json(str(template))
        (finding,) = payload['findings']
        assert set(finding) == {'severity', 'code', 'file', 'line', 'message'}
        assert finding['file'] is None
        assert finding['line'] is None

    def test_json_is_parseable_object(self, tmp_path):
        template = write_template(
            tmp_path / 'parseable',
            'question: answer\n',
            {'{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE},
        )
        result = run_lint(str(template), '--format', 'json')
        # Must be valid JSON with the three documented top-level keys.
        data = json.loads(result.stdout)
        assert set(data) == {'findings', 'errors', 'warnings'}
        assert isinstance(data['findings'], list)
        assert isinstance(data['errors'], int)
        assert isinstance(data['warnings'], int)


class TestRuntimeParity:
    """`copier lint` must accept every template `copier copy` would accept."""

    def _copy(self, template, destination):
        return subprocess.run(
            [
                sys.executable, '-m', 'copier', 'copy',
                '--defaults', '--quiet',
                str(template), str(destination),
            ],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=120,
        )

    def test_lint_clean_when_copy_succeeds(self, tmp_path):
        template = make_valid_template(tmp_path)
        destination = tmp_path / 'out-parity'
        copy = self._copy(template, destination)
        assert copy.returncode == 0, copy.stderr
        lint = run_lint(str(template))
        assert lint.returncode == 0, lint.stderr

    def test_multiple_config_files_rejected_by_both(self, tmp_path):
        template = tmp_path / 'multi-both'
        template.mkdir()
        (template / 'copier.yml').write_text('foo: bar\n', encoding='utf-8')
        (template / 'copier.yaml').write_text('foo: bar\n', encoding='utf-8')
        destination = tmp_path / 'out-multi'
        copy = self._copy(template, destination)
        assert copy.returncode != 0
        code, payload = lint_json(str(template))
        assert code == 1
        assert 'multiple-config-files' in {
            f['code'] for f in payload['findings']
        }

    def test_lint_uses_same_jinja_env_as_runtime(self, tmp_path):
        # The template uses the built-in ansible filter extension. Runtime
        # renders it correctly; lint must accept it because it loads the
        # very same extensions.
        template = write_template(
            tmp_path / 'ansible-filter',
            'name:\n    type: str\n    default: world\n',
            {
                '{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE,
                'greeting.md.jinja': 'hello {{ name | to_nice_yaml }}\n',
            },
        )
        lint = run_lint(str(template))
        assert lint.returncode == 0, lint.stderr


class TestExistingFeaturesIntact:
    """The pre-existing copier behavior must keep working."""

    COPY_TEMPLATE_CONFIG = (
        'project_name:\n'
        '    type: str\n'
        '    default: demo\n'
        'greeting:\n'
        '    type: str\n'
        '    default: hello\n'
    )

    def test_copy_still_renders(self, tmp_path):
        template = write_template(
            tmp_path / 'tpl',
            self.COPY_TEMPLATE_CONFIG,
            {
                '{{ _copier_conf.answers_file }}.jinja': ANSWERS_FILE_TEMPLATE,
                'README.md.jinja': (
                    '# {{ project_name }}\n{{ greeting }} world\n'
                ),
            },
        )
        destination = tmp_path / 'out'
        result = subprocess.run(
            [
                sys.executable, '-m', 'copier', 'copy',
                '--defaults', '--quiet',
                str(template), str(destination),
            ],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        readme = destination / 'README.md'
        assert readme.read_text(encoding='utf-8') == '# demo\nhello world\n'
        assert (destination / '.copier-answers.yml').is_file()

    def test_help_lists_lint(self):
        result = subprocess.run(
            [sys.executable, '-m', 'copier', '--help'],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0
        assert 'lint' in result.stdout

    def test_version_still_works(self):
        result = subprocess.run(
            [sys.executable, '-m', 'copier', '--version'],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0

    def test_check_update_command_still_reachable(self):
        result = subprocess.run(
            [sys.executable, '-m', 'copier', 'check-update', '--help'],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0

    def test_copy_command_still_reachable(self):
        result = subprocess.run(
            [sys.executable, '-m', 'copier', 'copy', '--help'],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0

    def test_lint_help_mentions_json(self):
        result = subprocess.run(
            [sys.executable, '-m', 'copier', 'lint', '--help'],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0
        assert 'json' in result.stdout.lower()
