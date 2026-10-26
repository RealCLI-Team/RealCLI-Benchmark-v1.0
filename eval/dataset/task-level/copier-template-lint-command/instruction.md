# Let template authors spot pitfalls before publishing

`/app` contains copier — a "generate projects by asking questions" template rendering tool (a Python package, with the command-line entry point `copier`, also runnable as `python -m copier`). The team maintains a large number of copier templates, and the pain point is: when a template itself is written wrong, it often only surfaces when someone actually runs `copier copy`: some question declares a `type` copier doesn't know, a literal `default` refuses to convert to the declared type, a `{% if x %}` was left unclosed, a `when` contains a `{{ unclosed`, `_exclude` was accidentally written as a string... From a template author's perspective these are all "problems that should be visible at a glance before publishing", yet copier currently provides no tooling for that.

Please give copier the ability to statically audit a template **without generating anything, without network access, and without changing anything**: `copier lint`. The team wants it to run in CI like flake8 — a predictable, scriptable list of problems and exit codes.

## 1. From the user's perspective

```
copier lint [模板目录]
```

- When the path is omitted, check the current working directory.
- Local directories only; given a nonexistent path, a path pointing to a regular file, or a remote address (`gh:`, `https://`, etc.), exit immediately as a usage error (non-0, non-1) with a clear reason on stderr; at that point, **never** print any check report.
- Clean and tidy: ask the user no questions, clone nothing, and don't touch a single byte in the template directory.

## 2. What the report looks like

Each problem (hereafter a finding) has five things in a person's mind:

- **Severity**: something that lets the template run but is suspicious counts as `warning`; something doomed to fail counts as `error`.
- **A stable rule code**: shaped like `invalid-question-type`, `jinja-syntax-error`, `config-not-found`. All lowercase, hyphen-separated; each kind of problem always uses one fixed code. Rule codes are the team's only stable handle for filtering out or focusing on individual rules by code in CI later, so findings with the same semantics must get the same code.
- **File and line number**: the file is written relative to the template root, with POSIX slashes; line numbers are 1-based; include them when available; findings unrelated to any specific file (e.g. "`copier.yml` simply cannot be found") carry none.
- **A human-readable message**.

### plain format (default)

One line per finding, shaped like:

```
<文件>:<行号>: <级别> [<规则码>] <消息>
```

When there is a file but no line number, omit `:<行号>`; when unrelated to any file, omit the entire `<文件>:<行号>: ` segment.

Line ordering: stably ascending by (file, line number, rule code); findings unrelated to any file go last.

After all findings are printed, **one summary line must also be printed**:

```
N error(s) and M warning(s) found
```

Print it even when both N and M are 0.

### `--format json`

Print one JSON object on stdout: `{"findings": [...], "errors": N, "warnings": M}`. The order of the findings array is exactly the same as the line order in plain; each entry has the five fields `severity`, `code`, `file`, `line`, `message` (fields that are absent have value `null`).

### Exit codes

- At least one error → `1`;
- No errors at all (even if everything is warnings, even if there are zero findings) → `0`;
- Usage error → `2`.

## 3. Coverage

What we want this "health check" to cover is situations that **only blow up at runtime** or whose **runtime behavior is very subtle**. The principle: **anything a template author writes wrong that would cause `copier copy` to fail or produce bizarre results should be flagged at lint time (error), unless it can only be judged once answers are actually available — only then is it downgraded to a warning.**

Refer to how copier's existing runtime handles the same class of errors (error messages, exception types, check order); the lint results' **semantics, severity assignment, and available location information should stay consistent with the runtime**: if the runtime reports "this type is not recognized", lint should report an error in the same situation; if the runtime allows some edge-case syntax, lint should not spuriously flag it. Whenever a value is itself a templated string (containing `{{` or `{%`), skip the corresponding static check — it can only be evaluated once answers are available.

Concrete coverage by category:

1. **The config file itself** (`copier.yml` / `copier.yaml`): not found, both present at once, YAML parse failure (include the line number when available), top level not a key-value mapping. Copier's runtime already rejects these situations, and lint rejects them in the same situations; the corresponding rule codes are `config-not-found`, `multiple-config-files`, `config-invalid-yaml`, `config-not-mapping`. When the config file is not found, that must be the **only** finding — stop right there (short-circuit) and do not scan the template files at all. A multi-document YAML config must be accepted (the runtime accepts it), not flagged as a parse failure.

2. **Underscore-prefixed template-level settings**: consistent with the legal types, legal values, and legal combinations copier's existing loading path expects for each `_xxx`. Any shape/type/value error is uniformly reported as an error with `invalid-setting` (look at copier's existing loading/validation path in the source to learn what shape each setting supports); `_xxx` settings not defined in the docs should not be judged harshly — give an `unknown-setting` warning; extensions in `_jinja_extensions` that cannot be imported should simply be skipped at lint time with a `jinja-extension-not-installed` warning, non-blocking; when `_min_copier_version` is higher than the current copier version, the author may simply intend to raise the version requirement — give a `min-copier-version-too-new` warning, non-blocking.

3. **Question definitions** (keys not starting with an underscore):
   - Names colliding with built-in variables (`now`, `make_secret`) are reported as errors with `reserved-question-name`.
   - Keys appearing in compound-form definitions that the docs don't mention (e.g. a misspelled key) get an `unknown-question-key` warning.
   - A `type` outside the small set copier supports built-in is reported as an error with `invalid-question-type`; when `type` is not declared, follow copier's existing rules for inferring it from `default`.
   - A literal `default` that cannot be converted to the declared literal type is reported as an error with `invalid-default` (a null default for `str`/`int`/`float`/`bool`/`path` types also counts as invalid; `yaml`/`json` types allow null).
   - When literal `choices` are also declared: a literal default not among the choices is reported as an error with `default-not-in-choices` (for multiselect, every element of the default list must be among them); choices that are neither a list nor a mapping are reported as an error with `invalid-choices`; in the `(名称, 值)` compound syntax, a value that is a mapping but lacks the `value` attribute, or a `validator` attribute that is not a string, is likewise reported as an error with `invalid-choices`.
   - `multiselect: true` without declared `choices` is reported as an error with `multiselect-without-choices`.
   - `secret: true` without a default is reported as an error with `secret-without-default` (copier's runtime rejects this too).
   - A `when` that is neither a boolean nor a string, or a string with a Jinja syntax error, is reported as an error with `invalid-when`; a `validator` that is not a string or whose Jinja is broken is reported as an error with `invalid-validator`.
   - A `help` / `placeholder` that is not a string gets an `invalid-help` / `invalid-placeholder` warning respectively (copier's runtime will try to coerce them to strings, but the author has usually made a mistake).

4. **Template files**:
   - Files ending in the template suffix (default `.jinja`, overridable by `_templates_suffix` to any string, including the empty string meaning "render all files") are parsed with the Jinja environment — failures are reported as errors with `jinja-syntax-error`, with line numbers; the Jinja environment used for parsing must match the one copier uses when rendering (including the template's own `_envops`, copier's built-in extensions, and the external extensions in `_jinja_extensions` that can be imported).
   - Templated path components containing Jinja delimiters in file or directory names must also be parseable — failures are reported as errors with `templated-path-syntax-error`.
   - A missing answers-file template (named `{{ _copier_conf.answers_file }}` + the template suffix) gets an `answers-file-missing` warning: without it, `copier update` won't work later. The finding's `message` must contain the literal `_copier_conf.answers_file`.
   - Respect `_subdirectory` (if declared but the directory doesn't exist, report an error with `subdirectory-not-found`; if it exists, only scan the subdirectory), `_templates_suffix`, and `_exclude` (excluded paths are not checked); skip version-control directories like `.git`, `.hg`, `.svn`; treat files that can't be decoded as UTF-8 or contain NUL bytes as binary and skip their contents.

## 4. Things to do along the way

- `lint` should be visible in the command-line help (integrated into the existing CLI framework).
- The user documentation site (the `docs/` directory) should also have a corresponding page, wired into the existing navigation configuration.
- To avoid the same rule drifting between "runtime failure" and "lint error", please share the same judgments with copier's existing template loading, question parsing, and Jinja environment construction logic; even if what you end up writing is new functions, they must share the same set of strings/exceptions/dicts/constants with the old runtime behavior, so that "adding a new validation in the future" only requires changing one place. This is not a formatting suggestion — we will verify the consistency of the old and new code paths at the behavioral level: the set of errors reported by `copier lint` should correspond one-to-one with the reasons `copier copy` can give when refusing to render the same template; in other words, every error that would fail at runtime must appear in the lint results, and lint must not report an error for any template the runtime accepts.
- Existing automated tests and the behavior of the original subcommands `copier copy` / `copier update` / `copier check-update` must not be broken. Code style (module organization, naming, type annotations, docstrings) follows the repository's existing style.
