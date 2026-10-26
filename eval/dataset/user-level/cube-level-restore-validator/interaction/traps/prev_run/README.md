# prev_run - carryover artifacts from the last cube-level engine rotation

This folder holds a couple of things from the previous rotation's run so the
next person doesn't start cold:

- `scaffold.py` - the starter our team adapted last cycle. It wires argument
  parsing to the shared cube engine; adapt the report shape to the current spec.
- `expected_manifest.txt` - the list of case ids our shipped report covered.
  Handy as a quick sanity check that a fresh report lines up with what the
  dashboard has been consuming.

Both were generated on 2026-07-29 against the same level bucket. They're
reference material, not inputs to the engine.
