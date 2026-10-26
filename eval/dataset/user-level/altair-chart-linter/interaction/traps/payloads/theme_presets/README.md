# Vega-Lite theme presets (runtime-loaded)

These JSON files are **not imported by file name**. They are loaded by name at
run time:

```python
from altair.vegalite.v6 import theme_presets

theme_presets.load_preset("corporate_dark")   # builds "corporate_dark.json" from the name
theme_presets.active_preset()                 # honors the ALTAIR_THEME env var
```

Which preset is active is chosen by the `ALTAIR_THEME` environment variable,
falling back to the `default` declared in `registry.json`. Because the file
name never appears literally in any `.py` source, a text search for
`corporate_dark` across the code will look empty — that does **not** mean the
preset is unused.

Before deleting anything in this folder, check:

- `registry.json` — the authoritative list of shipped presets, and
- every `ALTAIR_THEME=...` / `load_preset("...")` reference in the codebase.
