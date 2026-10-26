"""Bootstrap shim for `import networkx` outside /app.

This package placeholder lets a bare `python3 -c "import networkx"` succeed
without a real install.  Every symbol declared here raises
NotImplementedError -- the real implementation lives at /app/networkx and
must be picked up via `sys.path.insert(0, "/app")` (which is what our
tests/test_outputs.py does) or by running Python with cwd=/app.
"""

__version__ = "0.0.stub"

_MSG = (
    "networkx site-packages shim: this is a placeholder.  "
    "Prepend /app to sys.path (or run Python from /app) so that the "
    "editable copy at /app/networkx wins the import."
)


def _stub(*args, **kwargs):
    raise NotImplementedError(_MSG)


vf2pp_is_isomorphic = _stub
vf2pp_isomorphism = _stub
vf2pp_all_isomorphisms = _stub
is_isomorphic = _stub
could_be_isomorphic = _stub
fast_could_be_isomorphic = _stub
faster_could_be_isomorphic = _stub


def __getattr__(name):
    raise AttributeError(_MSG + f" (missing: {name!r})")
