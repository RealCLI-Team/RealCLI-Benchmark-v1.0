# Migrating from v1.x to the current copier flow

Downstream teams occasionally hit this page while porting older
templates. The walkthrough focuses on the mechanical answers-file rename
and the associated jinja-syntax updates.

## Before you start

- Keep your v1 template intact somewhere; the migration is one-way.
- The v1 `answers.yml` file becomes `copier.yml`; the shape of each
  question changes from bare mapping keys to typed blocks with `type`
  and `default`.

## Sample rewrite

Old:

```yaml
project_name: my-project
license: MIT
```

New:

```yaml
project_name:
    type: str
    default: my-project

license:
    type: str
    default: MIT
    choices:
        - MIT
        - Apache-2.0
```

## Related pages

- `v1.md` — the pre-migration layout reference.

We keep this page around because a couple of well-linked upgrade
walkthroughs on external sites still point here.
