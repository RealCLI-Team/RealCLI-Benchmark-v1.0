# cruft update trampled our local config — please "hand back" this class of files to the project

`/app` is the source repository of cruft (a Python package whose CLI entry point is `cruft`, also runnable as `python -m cruft`). cruft's job is: a project is initially generated from a cookiecutter template, and as the template keeps evolving afterwards, we rely on
`cruft update` / `cruft diff` / `cruft check` to propagate and compare upstream template changes against the already-generated project.

The problem we (template authors + users, two groups) are hitting is roughly this—

From the template author's perspective: templates contain a class of files that are **destined to be locally modified in every generated project**: the environment-specific
`config.ini`, the development `settings/` directory, boilerplate files that get tweaked all the time... The template of course has to ship a "starting point" version when generating, but **after generation they are maintained by the project itself**. We want template authors to be able to explicitly **declare** this class of paths, so that cruft treats them in subsequent processing as "owned by the project — I no longer manage them".

From the user's perspective, the current pain points include:

- `cruft update`: a template evolution upgraded the default values of `config.ini`; run update once, and the user's local version either gets
  overwritten by the three-way merge, or `.rej` conflict files get scattered all over the project. Users have asked dozens of times in the group chat "what is this `config.ini.rej` and do I need to deal with it".
- `cruft update`: the template author added a new boilerplate file like `settings/extra.ini` that "users would write themselves"; run update
  once and it gets stuffed into the user's project out of thin air — the user never wanted it.
- `cruft update`: conversely, the template author deleted some "file that always belonged to the project" from the new version; the user of course still has a local copy
  they have maintained for years — and update deletes that file along with it. User data gone.
- `cruft update`: the user deliberately deleted a "locally owned" file from the project (they don't want it anymore). Run update once,
  and cruft "restores" it from the template. The user wants it gone but can't get rid of it.
- `cruft diff`: in CI we run `cruft diff --exit-code` to detect "is the project behind the template". But the user's local
  `config.ini`, `settings/local.ini` always differ from the template, the diff is never empty, and `--exit-code` always returns non-zero
  — completely unusable in CI. And even when eyeballing the diff, the noise from that pile of local files drowns out the template differences
  that actually need attention.

We want these pain points solved once and for all **from the angle of what template authors can declare**. The proposal is to allow the template author to provide a new declaration file in the **root directory of the generated project**
(something gitignore-like: a plain text file, one pattern per line), listing "these paths belong to the project — cruft, please don't touch them anymore". The file name follows industry convention (`.cruftignore`). It is itself an ordinary template
file — it gets rendered when the project is generated (cookiecutter variables appearing in it are substituted like in any other file), it shows up in the project as usual, and afterwards it keeps evolving with the template (users can of course also edit it locally).

Please implement this capability. The externally visible behavior is defined by the user scenarios below; the internal implementation is up to you, but it must blend into the repository's existing code style, existing
utility functions, and the overall skeleton of the existing commands — do not start a parallel processing path off to the side.

## User acceptance scenarios

### `cruft update`

- For paths matched by this declaration file in the new template version, the user's files on disk **always win**. cruft must not create, must not modify, must not
  delete them; new matched files appearing in the new template version must not enter the project; old matched files removed from the new template version stay in the project;
  matched files the user deleted themselves must not be "restored" either.
- Matched paths **must never produce `.rej` conflict files** — regardless of whether update takes the three-way merge or the `--reject` fallback path,
  this holds. A user scanning the project for `.rej` files must not see the name of any path that was matched.
- When update finishes, the user needs to know "which declared-matched paths this update skipped", so they understand what the template actually evolved
  into. **But only print paths that, in this update, "would have been changed by this evolution if not ignored"** — paths that this update
  didn't touch at all and are merely matched by the declaration must not appear in this notice. This notice must be easy to recognize by machines and easy to align visually, with a fixed
  format: one path per line, each line's content exactly `Skipping ignored path: <路径>` (the prefix followed by one space and then the path,
  with no extra decorative text before or after); `<路径>` must be written in **POSIX style (forward-slash separated), relative to the project root**,
  and these lines must be sorted **alphabetically by path**. (Terminal coloring wrapping the whole line is allowed, but after stripping ANSI color codes the line content must match the above
  verbatim.)
- The rule set for deciding "which paths are matched" must come from **the target version of this update** (i.e. the declaration file
  rendered from the new template version). Whether the old template version has this file, what it says, is irrelevant to this update's skip results. Paths that were
  ignored in the old version but are no longer ignored in the new version should be managed normally by the template in this update.
- If the user has edited this declaration file locally, when running update — it is just like any other ordinary template file and gets processed through update's
  regular flow (e.g. three-way merge, `--reject` fallback). In other words: this declaration file itself is **never ignored by itself or by any other
  rule**, even if the rule set contains wildcards like `*`, `**`, `.*`.
- All other existing capabilities must remain unaffected: interactive confirmation (the user typing `y` / `n` / `s` /
  `v` at the `Apply diff and update?` prompt), `-y` one-shot confirmation, `--skip-update`, `--checkout`, `--strict`, the project living in a subdirectory of a git repository, the template
  living in a subdirectory of its git repository, `extra_context`, three-way merge and `--reject` fallback, `.cruft.json`
  advancing normally after update... all must work as before. When the user types `v` in interactive mode to view this update's differences, matched paths **must not
  appear in the diff they see**.
- The existing `skip` field in `.cruft.json` (including `pyproject.toml [tool.cruft] skip`) remains effective; the two mechanisms
  don't interfere with each other and can stack: a path matched by either side must be protected.

### `cruft diff`

- Differences of matched paths **must not appear in the diff output**, and must not affect the `--exit-code` decision — if only matched
  paths differ, `--exit-code` should return 0.
- The rule set comes from the declaration file rendered by **the template version being compared** (i.e. the version specified by `--checkout`, or the version recorded
  in the project's `.cruft.json`).
- The output of `cruft diff` must remain a **clean patch text that can be consumed directly by `git apply` / `patch`** — no
  extra notice text may be mixed into it.

### `cruft create` and `cruft check`

- `cruft create`: completely unaffected. Rendering still outputs every template file (including this declaration file itself, and all other files that would be
  matched by it) into the project — after all, the **starting point** of "these files are owned by the project" must be generated first for the user to have
  something to maintain.
- `cruft check`: completely unaffected; the existing semantics of "using the commit hash to decide whether the project is behind the template" stay as they are.

## Engineering requirements

- Do not break any existing behavior of `cruft create / update / diff / check / link`. The existing pytest unit cases in the repository under `/app`
  that can run offline (the ones related to `cruft._commands.utils`, and the ones related to exceptions) **must continue to all pass**
  after your changes; no new failures may be introduced by your changes.
- Please add documentation to the corresponding user docs (`README.md`).
- Keep the code style consistent with the repository's existing code; add sufficient unit and integration test cases for the new capability — integration tests use locally constructed temporary git
  template repositories and must not access the network.
