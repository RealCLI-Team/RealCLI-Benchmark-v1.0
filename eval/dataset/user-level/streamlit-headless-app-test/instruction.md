# Our internal streamlit apps can't run tests in CI

Our team built a bunch of internal data tools with this copy of streamlit (under /app). Every change requires manually opening a browser and clicking through it — we're fed up.

I did some research and found that the /app repo already has something related to "run a script + assert output + simulate widgets" — that pile of stuff in `lib/streamlit/testing/`. But its problems are legion:

- It's designed for streamlit's own developers to write unit tests, and the interface is too awkward. Writing one case means subclassing `unittest.TestCase`, constructing a runner, running the script, grabbing elements, then manually rerunning — tons of boilerplate. We want app developers to write tests from a natural user perspective, like this:

  ```python
  from streamlit.testing import AppTest  # 或者从 streamlit.testing.v1 里也拿得到

  at = AppTest.from_string("""
      import streamlit as st
      name = st.text_input("who")
      if st.button("greet"):
          st.write(f"hi {name}")
  """).run()

  at.text_input[0].input("ada")
  at.button[0].click().run()
  assert at.markdown[0].value == "hi ada"
  ```

  In other words, `AppTest` is the main entry point of this framework and should be constructible from three sources: a script file, an inline string, and a Python function (whose body is treated as the script). After `.run()`, you should be able to get lists by element type (`at.button`, `at.text_input`, `at.markdown`, etc.), and call action methods on widgets that mirror frontend semantics (click, check, input, increment/decrement, select/unselect, drag to some tick, set date/time/color...). Calling an action and then `.run()` is equivalent to the frontend sending the new widget state back and triggering a rerun. Chaining should be fluent — method chains like `.click().run()`, `.input('x').run()` should just work.

- What we want is "looks like it runs in a browser, but doesn't actually start a browser, an http service, or a websocket connection". It must run to completion in CI or locally with a single `pytest`. When a script gets stuck (e.g. someone mistakenly wrote `time.sleep(...)`), the test process must not wait forever — there must be a timeout; a default timeout can be set when constructing `AppTest`, and a single `run` can override it; after a timeout, the script thread must be stopped and an obvious error raised telling me this was a timeout.

- Our apps rely heavily on `st.session_state` and query parameters. Before starting a test we often need to preset part of the state (e.g. "assume the user is already logged in, `session_state['user']='ada'`") or preset query parameters (`?tab=daily`) to steer the script into a certain branch. After the run, I need to read the `session_state` as modified by the script. For widgets with a `key`, changing the widget value and reading `session_state[key]` should show the same thing.

- Our apps make heavy use of `st.cache_data` (caching slow queries). This decorator must not lose its effect just because we're in a test environment — repeated `run()`s of the same AppTest should see the cache in effect (the same uuid or whatever stays the same).

- The main area and the sidebar must be separated. Some of our tools put the control panel in `st.sidebar`; when testing I want to query only those widgets in the sidebar without interference from buttons in the main area, and vice versa the main area must not be polluted by sidebar stuff. `at` itself, `at.main`, and `at.sidebar` should all support indexing, `len()`, and iteration.

- Button semantics must match the real frontend: within one `.click().run()`, the script sees `if st.button(...)` as True; on the immediately following `run()` it should revert to False (the frontend doesn't hold a button down forever). We've been bitten by this, and really need it.

- An exception in the script must not take down the whole test process. The exception should appear in the tree as an exception element, and I should be able to get its text (including the exception name and message) and stack information.

- Before one `.run()`, I can modify several widgets in a row, and all changes take effect together on the next `run` (corresponding to the frontend submitting multiple widget updates in one batch).

- Static display elements (`st.title` / `st.header` / `st.subheader` / `st.markdown` / `st.caption` / `st.text` / `st.code` / `st.latex` / `st.divider` / `st.info` / `st.success` / `st.warning` / `st.error`) must all be queryable — our apps use them everywhere to post status. Alerts like `st.warning("配置缺失")` are all over our apps, and I need to assert in tests that "under this branch that specific warning actually appeared, not an info".

- Generic element lookup by type: queries like `at.get("markdown")`, `at.get("button")` must also exist.

- The old `InteractiveScriptTests` in `lib/streamlit/testing/` (in `script_interactions.py`) cannot be cut outright — the repo already has a batch of tests written against it, and deleting it would break the entire repo's test suite. So we need a "still usable but clearly deprecated" path: the old API stays usable (existing code like `class XxxInteractiveTest(InteractiveScriptTests)` still runs), but instantiating it must emit a standard Python `DeprecationWarning` (the `warnings.warn` kind, catchable with `pytest.warns(DeprecationWarning)`), whose message mentions migrating to the new `AppTest`. Likewise, importing `AppTest` directly from the old top-level path `streamlit.testing` should also give the new class — we don't want users guessing which submodule to import from.

- Code style, type annotations, and license headers must stay consistent with the rest of the repo.

The framework itself also needs a batch of unit tests covering the behaviors above — we want a safety net for future feature additions.
