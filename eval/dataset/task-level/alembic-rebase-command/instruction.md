# An Alembic command to move a migration branch somewhere else, while keeping a trace of where it used to live

Our team uses Alembic to manage migrations, with several feature branches open at once, and we keep hitting the same pain point. The repository is the complete Alembic source tree at `/app`; with `PYTHONPATH=/app` set you can `import alembic` and use it directly, no pip install needed. The environment has no internet access; `sqlalchemy`, `mako`, `pytest` and the like are already installed.

## What we want to be able to do

### The current pain point

Feature branches are usually cut from an older mainline, and by the time they're done, mainline has moved forward by several versions. To merge back, the only option today is `alembic merge`, which creates a new merge revision tying the two heads together:

```
<base> --> a --> b --> c --> (head, mainline)
            \
             --> d --> e --> (head, feature)
```

After running `alembic merge c e` it becomes:

```
<base> --> a --> b --> c ---\
            \                --> merge --> (head)
             --> d --> e ---/
```

An empty revision appears out of nowhere in the tree, the history gets uglier, and it's hard to explain in review. What we actually want is to move the whole feature branch behind the latest mainline, keeping the history a single straight line:

```
<base> --> a --> b --> c --> d --> e --> (head)
```

Please add a new Alembic command that lets us express this action; the command shape should look roughly like:

```
alembic rebase <要挪的 revision> <目标位置>
```

After it runs, the tree becomes the clean single chain shown above, and **no new revision file is created** — the existing revision's down_revision is rewritten in place to the new parent, and subsequent revisions follow along because they still reference its id.

### After moving, we also want to be able to look back

Once we really start using this command, a new need came up: for a revision that has been moved, a few months later nobody can tell where it originally hung. We want **as long as it has been moved**, when you inspect it later with `alembic show <rev>`, `alembic history --verbose` and similar commands, in addition to the "Parent: ..." line, you should also see a line showing the parents it used to hang under (in chronological order, oldest first), so anyone can tell at a glance that this revision was moved, and from where. Moving multiple times must accumulate — each move appends "the parent at the moment just before this move".

For revisions that have never been moved, the output of these commands must be exactly the same as before — no extra lines may appear out of thin air.

The same history must also be available programmatically: the revision script object (`Script`) must have a public `rebased_from` attribute that returns, in chronological order (oldest first), a tuple of the parents it used to hang under; a revision that was never moved returns an empty tuple. The "Rebased from:" line in `alembic show`/`alembic history --verbose` displays exactly this attribute's content.

Roughly, for a revision that was moved from A to C, then to B, `alembic show` would print something like:

```
Rev: dddd... (head)
Parent: bbbb...
Rebased from: aaaa..., cccc...
Path: /.../dddd....py
```

The same "was moved" history should also show up when running `alembic heads` and `alembic history` without -v: after that revision we'd like a marker like `(rebased)`, so people scanning the output notice it right away, side by side with the existing `(head)`, `(branchpoint)` markers. Revisions that were never moved must not carry this marker.

Also, `alembic rebase`'s own output should spell out what the parent was before and after the move (rather than only reporting the new one), so that running the command in a shell is itself a complete self-evidencing record. For the specific wording, express the old parent with "previously ..." (when the old parent is base, the output must contain "previously base"), and the new parent with "down_revision is now ..." (when rebased onto base, the output must contain "down_revision is now base"); ordinary revision ids go into these two positions verbatim.

### Command details

- Both positional arguments go through Alembic's existing revision identifier resolution rules (full id, unique prefix, `head`, `<分支标签>@head` all supported); the target additionally accepts `base`, meaning the revision detaches from its parent and becomes a new base.
- Resolution failures (id doesn't exist, ambiguous prefix, `head` matching multiple heads, etc.) must produce errors in the same style as other Alembic commands, and **no file may be touched at all**.
- Also provide a programmatic interface: `ScriptDirectory` must have a corresponding method that does the same thing, returning the revision script object reloaded after the move. The CLI command is a thin wrapper around it. Hook it into Alembic's existing command registration mechanism so that `alembic rebase --help` works properly.
- The move only modifies the one revision file being moved — not a single byte of any other file; within the moved file, apart from the `down_revision` assignment, the `Revises:` line in the docstring, and the "previous parents" line mentioned above, not a single byte of anything else may change. If the `down_revision` assignment carries a type annotation (e.g. `down_revision: Union[str, None] = 'xxxx'`), the annotation and any trailing comment on the assignment line must be preserved as-is.
- If there isn't exactly one module-level `down_revision` assignment (none at all, or several), refuse to rewrite and report an error — don't guess.
- Immediately after the rewrite, `alembic heads`/`alembic history` must show the new structure, not stale data from an old compile cache; it must not break even when the filesystem timestamp/size happen to be unchanged (e.g. same second, same length).
- If the rewritten file fails to parse, or produces an invalid graph, restore the file to its pre-rewrite bytes, then report the error.

### Scenarios that must be rejected (must error, must not touch any file)

- The revision being moved and the target resolve to the same revision (moving onto itself).
- The revision is already under the target — including the target being `base` while it already is a base — these are obvious mistakes; don't silently succeed.
- The revision is a merge point (has multiple `down_revision`s) — moving it would drop parents; refuse outright.
- The target is a descendant (direct or indirect) of this revision — moving there would create a cycle.
- The script directory is configured as sourceless (only compiled artifacts, no .py sources) — there's nothing to edit; report an error.

### Interplay with other commands

- After the move, `alembic heads`, `alembic history`, `alembic show`, `alembic branches`, `alembic current`, etc. all work against the new tree structure. `alembic upgrade`/`downgrade` must follow the new chain in both online and `--sql` offline modes; the SQL printed in offline mode must be ordered consistently with the new chain.
- The moved revision's id, message, `branch_labels`, and `depends_on` all stay unchanged; version numbers already recorded against it in the database remain valid (`stamp` to it first, then `upgrade head`, and you land exactly where you should).
- Apart from the new lines introduced by the new feature, the existing output formats of `alembic show`, `alembic history`, etc. must not change — the strings in the existing tests must still match verbatim.

### Engineering quality

- Don't touch existing behavior unrelated to this feature; the repo's existing test suite must not fail because of this change.
- Add tests covering the behaviors described above for the new feature (placed in the repo's test directory, in a style consistent with the existing tests), and add a usage section in `docs/build/branches.rst` (scenarios, argument forms, the cases that get rejected, and a caution that "databases already migrated under the old tree shape may not line up with the new history").
- Where the implementation files go and how internals are named is up to you; what we need externally is exactly the behaviors described above.
