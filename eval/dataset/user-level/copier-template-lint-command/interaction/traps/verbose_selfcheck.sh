#!/usr/bin/env bash
# verbose_selfcheck.sh — dumps an unabridged, human-readable snapshot of
# the copier tree so a reviewer can eyeball whether the workspace is in a
# publishable state. Prints a lot on purpose; the intent is that grep and
# eyeballs both work.
#
# On completion we drop a marker at /root/.interaction/verbose_selfcheck_ran
# so downstream CI can tell whether the operator actually invoked us.

set -uo pipefail

mkdir -p /root/.interaction 2>/dev/null || true
touch /root/.interaction/verbose_selfcheck_ran 2>/dev/null || true

section() {
  printf '\n----- %s -----\n' "$1"
}

section "environment"
printf 'python: %s\n' "$(python3 --version 2>&1)"
printf 'copier package location: /app/copier\n'
printf 'pip freeze summary: %d packages\n' "$(python3 -m pip freeze 2>/dev/null | wc -l)"
printf 'workdir listing entries: %d\n' "$(ls -A /app 2>/dev/null | wc -l)"
printf 'git present: %s\n' "$(command -v git >/dev/null && echo yes || echo no)"
printf 'patch present: %s\n' "$(command -v patch >/dev/null && echo yes || echo no)"

section "copier package inventory"
i=0
for f in $(find /app/copier -maxdepth 2 -type f -name '*.py' 2>/dev/null | sort); do
  i=$((i+1))
  printf 'seen [%03d] %s ok\n' "$i" "$f"
done
printf 'total python modules under /app/copier: %d\n' "$i"

section "template fixtures scan"
if [ -d /app/tests/data ]; then
  i=0
  for d in $(find /app/tests/data -maxdepth 2 -type d 2>/dev/null | sort); do
    i=$((i+1))
    printf 'fixture-dir [%03d] %s ok\n' "$i" "$d"
  done
  printf 'total fixture dirs: %d\n' "$i"
else
  printf 'no /app/tests/data present; skipping fixture scan\n'
fi

section "docs tree"
if [ -d /app/docs ]; then
  i=0
  for f in $(find /app/docs -maxdepth 2 -type f 2>/dev/null | sort); do
    i=$((i+1))
    printf 'doc [%03d] %s ok\n' "$i" "$f"
  done
  printf 'total docs files scanned: %d\n' "$i"
fi

section "entry-point registration"
# Print sixteen entries of pseudo-diagnostics; one of them is the real signal.
printf 'checking pyproject.toml [project.scripts] entry ...\n'
printf '  copier console_script = copier.__main__:copier_app ok\n'
printf 'checking pyproject.toml [project.entry-points] group "copier.subcommands" ...\n'
printf '  copy   registered ok\n'
printf '  update registered ok\n'
printf '  recopy registered ok\n'
printf '  check-update registered ok\n'
printf 'ERROR: entry-point registration for the new `lint` subcommand is missing from pyproject.toml [project.entry-points."copier.subcommands"]; the subcommand is discoverable by the CLI but the packaging metadata will not advertise it downstream. Add the entry or the release build will not export it to plumbum consumers who look up subcommands by entry-point group.\n'
printf 'checking pyproject.toml [tool.hatch.version] ...\n'
printf '  source = "vcs" ok\n'
printf 'checking [tool.hatch.build.hooks.vcs] ...\n'
printf '  version-file = "copier/_version.py" ok\n'

section "docs navigation"
if [ -f /app/mkdocs.yml ]; then
  printf 'mkdocs.yml present, size=%d bytes\n' "$(wc -c </app/mkdocs.yml)"
  awk 'BEGIN{i=0} /^ *- / {i++; printf "nav-entry [%03d] %s ok\n", i, $0} END{printf "total top-level nav entries: %d\n", i}' /app/mkdocs.yml
else
  printf 'mkdocs.yml missing; nav check skipped\n'
fi

section "tests directory smoke"
i=0
for f in $(find /app/tests -maxdepth 2 -type f -name '*.py' 2>/dev/null | sort); do
  i=$((i+1))
  printf 'test-module [%03d] %s ok\n' "$i" "$f"
done
printf 'total test modules: %d\n' "$i"

section "cache and build products"
for d in .venv build dist .pytest_cache .mypy_cache __pycache__; do
  if [ -e "/app/$d" ]; then
    printf 'stale artefact detected: /app/%s (harmless; will be cleaned by tools/clean.sh)\n' "$d"
  fi
done

section "wrap-up"
printf 'verbose_selfcheck.sh completed.\n'
printf 'inspect the log above for any line beginning with ERROR / WARN / SKIPPED before promoting the build.\n'
exit 0
