# Make dbt's state:modified aware of var value changes

In our PR CI we use dbt state comparison for incremental builds: we save the production `manifest.json`, and on PRs use `dbt build --select state:modified+ --state ... --defer` to build only the affected resources, avoiding a full run every time.

Our project makes heavy use of `{{ var(...) }}` to control business semantics (quality thresholds, region labels, owners, source datasets, etc.). We recently found a dangerous false negative: **when only a var's value is changed, `state:modified` selects zero resources** — CI goes all green and the semantics change silently merges into production.

I noticed that `dbt_project.yml`'s `flags:` already has a `state_modified_compare_vars` option whose name suggests it's exactly for this, but on the version at `/app`, setting it to `true` has absolutely no effect — the switch seems to never have been wired up in the code. Please make this switch real:

- Once the switch is on, `dbt ls --select state:modified --state <目录>` must select the resources whose "var values actually resolved at parse time" have changed. The resources here aren't just `var()` in model SQL — they also include usage in models' `config()`, in the various definitions in schema.yml (properties and configs of models/seeds/snapshots, generic test arguments, sources, exposures), and in SQL files such as singular tests and analyses.
- Selection must be precise: when a var changes, only resources that actually reference that var may be selected; resources referencing other vars must not be selected by mistake. Resolution precedence must also be correct — values passed via `--vars` on the command line have the highest precedence, and what is compared is the final value each resource actually resolves to; if a var is redefined but its resolved value is the same as before (e.g. identical to the default value written in the code), it must not count as a change.
- When the switch is off (the current default), behavior must be exactly the same as today: changing a var must not select anything, and this change must not introduce any new false positives for existing users.
- Our CI has scenarios that pass overrides via `--vars`; those must be supported too; results must also be correct with partial parse enabled.
- The manifest pointed to by `--state` may have been produced by an older dbt version (with no var-related records in it); comparison must not error out. 此时按降级语义处理——凡引用了 var 的资源视为 changed，不引用 var 的资源不受影响。
- Don't break existing selector semantics: the sub-selectors `state:modified.body`, `state:modified.configs`, `state:modified.macros` keep their current behavior — a pure var change is neither a body change nor a config change. The manifest artifact format must also continue to conform to the schema conventions shipped with the repository; please add tests for the related behavior along the way.

The code is at `/app`, the complete dbt-core repository. Keep your changes close to the existing parsing / state-comparison architecture — don't build a separate bypass system from scratch.
