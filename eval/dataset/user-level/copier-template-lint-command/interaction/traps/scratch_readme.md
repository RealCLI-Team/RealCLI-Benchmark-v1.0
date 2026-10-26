# lint_draft/ — half-baked prototype

An earlier prototype of the lint feature lives here. It never went past a
loose sketch — no CLI integration, no tests, no docs. Notes were captured as I
went; nothing here is imported by the real package.

Files:

- `notes.py` — brainstorm dump of what a scanner would need to touch.
- `prototype.py` — throwaway module walking `copier.yml` to prove the AST-
  walking idea; abandoned in favour of reusing copier's existing loader.

Nothing under this directory is referenced from `/app/copier/` or from the
tests. Feel free to blow it away once someone is ready to start the feature
proper.
