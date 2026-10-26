"""Behavioral contract checks for the headless app testing API.

These checks exercise the public surface of the framework end to end:
constructing a runner from source, running scripts, reading the element
tree, simulating widget interactions, observing reruns, cache stability,
alert-style elements, and the compatibility contract with the old
``InteractiveScriptTests`` entry point that lives alongside it.

All expected values are frozen here and derived only from the documented
public contract, not from any particular implementation. Nothing below is
allowed to introspect implementation-defined internals such as private
attributes on widget objects.
"""

import datetime
import warnings

import pytest


# --- Public import surface -------------------------------------------------
#
# The framework exposes a single primary entry point, ``AppTest``. Users
# should not have to know which internal package revision is current — the
# short path ``streamlit.testing.AppTest`` must resolve to the same class as
# the versioned ``streamlit.testing.v1.AppTest`` and be importable at module
# load time (i.e. package ``__init__`` must re-export it).

from streamlit.testing import AppTest as TopLevelAppTest
from streamlit.testing.v1 import AppTest as V1AppTest


def test_top_level_apptest_alias_is_the_v1_class():
    assert TopLevelAppTest is V1AppTest


# Below this line the tests always go through the top-level alias, to
# guarantee the alias itself keeps working under real usage.
AppTest = TopLevelAppTest


# --- Construction ----------------------------------------------------------


def test_run_simple_script_from_string():
    at = AppTest.from_string(
        "import streamlit as st\nst.markdown('hello world')\n"
    )
    result = at.run()
    assert result is at
    assert len(at.exception) == 0
    assert [m.value for m in at.markdown] == ["hello world"]


def test_from_string_dedents_indented_source():
    # Users often paste indented source from their editor; the constructor
    # must tolerate a common leading indent.
    at = AppTest.from_string(
        """
        import streamlit as st
        st.title("indented")
        """
    )
    at.run()
    assert at.title[0].value == "indented"


def test_construction_from_function():
    def app():
        import streamlit as st

        st.title("function app")

    at = AppTest.from_function(app)
    at.run()
    assert at.title[0].value == "function app"


def test_construction_from_function_body_ignores_signature():
    # The signature line itself must not become executed script content — it
    # would raise ``SyntaxError`` if it did.
    def app_with_signature():
        import streamlit as st

        st.header("body only")

    at = AppTest.from_function(app_with_signature)
    at.run()
    assert len(at.exception) == 0
    assert at.header[0].value == "body only"


def test_construction_from_file(tmp_path):
    p = tmp_path / "the_app.py"
    p.write_text("import streamlit as st\nst.write('file body')\n")
    at = AppTest.from_file(str(p))
    at.run()
    assert at.markdown[0].value == "file body"


# --- Static text elements --------------------------------------------------


def test_static_text_elements():
    script = (
        "import streamlit as st\n"
        "st.title('T')\n"
        "st.header('H')\n"
        "st.subheader('S')\n"
        "st.text('plain')\n"
        "st.caption('cap')\n"
        "st.code('x = 1')\n"
        "st.divider()\n"
        "st.latex(r'a=b')\n"
    )
    at = AppTest.from_string(script)
    at.run()
    assert at.title[0].value == "T"
    assert at.header[0].value == "H"
    assert at.subheader[0].value == "S"
    assert at.text[0].value == "plain"
    assert at.caption[0].value == "cap"
    assert "x = 1" in at.code[0].value
    assert len(at.divider) == 1
    assert "a=b" in at.latex[0].value


def test_static_element_type_labels():
    # ``.type`` on the element must match the natural user-facing name that
    # ``at.<name>`` queries by — this is what makes ``at.get("markdown")``
    # equivalent to ``at.markdown``.
    script = (
        "import streamlit as st\n"
        "st.title('t')\n"
        "st.header('h')\n"
        "st.subheader('sh')\n"
        "st.markdown('md')\n"
        "st.caption('cp')\n"
        "st.divider()\n"
        "st.latex('lx')\n"
    )
    at = AppTest.from_string(script)
    at.run()
    assert at.title[0].type == "title"
    assert at.header[0].type == "header"
    assert at.subheader[0].type == "subheader"
    assert at.markdown[0].type == "markdown"
    assert at.caption[0].type == "caption"
    assert at.divider[0].type == "divider"
    assert at.latex[0].type == "latex"


# --- Alert elements --------------------------------------------------------


def test_alert_elements_are_categorised_by_kind():
    script = (
        "import streamlit as st\n"
        "st.info('info-body')\n"
        "st.warning('warn-body')\n"
        "st.error('err-body')\n"
        "st.success('ok-body')\n"
    )
    at = AppTest.from_string(script)
    at.run()

    assert [e.value for e in at.info] == ["info-body"]
    assert [e.value for e in at.warning] == ["warn-body"]
    assert [e.value for e in at.error] == ["err-body"]
    assert [e.value for e in at.success] == ["ok-body"]

    # A warning must NOT bleed into the info list, and vice versa — this is
    # the "I need to assert 'warning fired, not info'" case from the brief.
    assert len(at.info) == 1
    assert len(at.warning) == 1
    assert len(at.error) == 1
    assert len(at.success) == 1

    # Type labels line up with the natural ``at.<kind>`` accessor name.
    assert at.info[0].type == "info"
    assert at.warning[0].type == "warning"
    assert at.error[0].type == "error"
    assert at.success[0].type == "success"

    # Generic query works too.
    assert [e.value for e in at.get("warning")] == ["warn-body"]
    assert [e.value for e in at.get("info")] == ["info-body"]


def test_alerts_do_not_appear_in_markdown_or_text_lists():
    at = AppTest.from_string(
        "import streamlit as st\n"
        "st.warning('be careful')\n"
        "st.write('regular body')\n"
    )
    at.run()
    assert [m.value for m in at.markdown] == ["regular body"]
    assert len(at.text) == 0


# --- Widget properties -----------------------------------------------------


def test_widget_properties_exposed():
    script = (
        "import streamlit as st\n"
        "st.button('lab', key='k1', help='hint', disabled=True)\n"
    )
    at = AppTest.from_string(script)
    at.run()
    btn = at.button[0]
    assert btn.label == "lab"
    assert btn.key == "k1"
    assert btn.help == "hint"
    assert btn.disabled is True


def test_button_click_observed_by_script_once():
    script = (
        "import streamlit as st\n"
        "if st.button('go'):\n"
        "    st.write('yes')\n"
        "else:\n"
        "    st.write('no')\n"
    )
    at = AppTest.from_string(script)
    at.run()
    assert at.button[0].value is False
    assert at.markdown[0].value == "no"

    at.button[0].click().run()
    assert at.markdown[0].value == "yes"

    # the click is consumed by that run; the next run sees the button released
    at.run()
    assert at.markdown[0].value == "no"


def test_button_click_returns_self_for_chaining():
    at = AppTest.from_string(
        "import streamlit as st\nst.button('go')\n"
    )
    at.run()
    same = at.button[0].click()
    assert same is at.button[0]


def test_checkbox_roundtrip_with_session_state():
    script = (
        "import streamlit as st\n"
        "v = st.checkbox('agree', key='cb')\n"
        "st.write(f'value={v}')\n"
    )
    at = AppTest.from_string(script)
    at.run()
    assert at.session_state["cb"] is False

    at.checkbox[0].check().run()
    assert at.checkbox[0].value is True
    assert at.session_state["cb"] is True
    assert at.markdown[0].value == "value=True"

    at.checkbox[0].uncheck().run()
    assert at.session_state["cb"] is False
    assert at.markdown[0].value == "value=False"


def test_text_widgets():
    script = (
        "import streamlit as st\n"
        "a = st.text_input('name', value='init')\n"
        "b = st.text_area('bio')\n"
        "st.write(f'{a}|{len(b)}')\n"
    )
    at = AppTest.from_string(script)
    at.run()
    assert at.text_input[0].value == "init"

    at.text_input[0].input("ada").run()
    assert at.text_input[0].value == "ada"
    assert at.markdown[0].value == "ada|0"

    at.text_area[0].input("hello").run()
    assert at.text_area[0].value == "hello"
    assert at.markdown[0].value == "ada|5"


def test_number_input_controls():
    script = (
        "import streamlit as st\n"
        "v = st.number_input('n', min_value=0, max_value=10, value=4, step=2)\n"
        "st.write(f'n={v}')\n"
    )
    at = AppTest.from_string(script)
    at.run()
    assert at.number_input[0].value == 4

    at.number_input[0].increment().run()
    assert at.number_input[0].value == 6
    assert at.markdown[0].value == "n=6"

    at.number_input[0].decrement().run()
    assert at.number_input[0].value == 4

    at.number_input[0].set_value(8).run()
    assert at.number_input[0].value == 8
    assert at.markdown[0].value == "n=8"


def test_option_widgets():
    script = (
        "import streamlit as st\n"
        "r = st.radio('r', ['a', 'b', 'c'], index=1)\n"
        "s = st.selectbox('s', ['x', 'y', 'z'])\n"
        "m = st.multiselect('m', ['red', 'green'], default=['red'])\n"
        "st.write(f'{r}|{s}|{len(m)}')\n"
    )
    at = AppTest.from_string(script)
    at.run()
    assert at.radio[0].value == "b"
    assert at.radio[0].options == ["a", "b", "c"]
    assert at.selectbox[0].value == "x"
    assert at.multiselect[0].value == ["red"]

    at.radio[0].set_value("c")
    at.selectbox[0].select("z")
    at.multiselect[0].select("green")
    at.run()
    assert at.radio[0].value == "c"
    assert at.selectbox[0].value == "z"
    assert at.selectbox[0].index == 2
    assert sorted(at.multiselect[0].value) == ["green", "red"]
    assert at.markdown[0].value == "c|z|2"

    at.selectbox[0].select_index(0).run()
    assert at.selectbox[0].value == "x"

    at.multiselect[0].unselect("red").run()
    assert at.multiselect[0].value == ["green"]


def test_sliders():
    script = (
        "import streamlit as st\n"
        "a = st.slider('a', 0, 100, 30)\n"
        "b = st.slider('b', 0, 100, (20, 40))\n"
        "c = st.select_slider('c', ['s', 'm', 'l'], value='m')\n"
        "st.write(f'{a}|{b}|{c}')\n"
    )
    at = AppTest.from_string(script)
    at.run()
    assert at.slider[0].value == 30
    assert at.slider[1].value == (20, 40)
    assert at.select_slider[0].value == "m"

    at.slider[0].set_value(70)
    at.slider[1].set_range(10, 90)
    at.select_slider[0].set_value("l")
    at.run()
    assert at.slider[0].value == 70
    assert at.slider[1].value == (10, 90)
    assert at.select_slider[0].value == "l"
    assert at.markdown[0].value == "70|(10, 90)|l"


def test_date_time_color_widgets():
    script = (
        "import streamlit as st\n"
        "import datetime\n"
        "d = st.date_input('d', datetime.date(2023, 3, 14))\n"
        "t = st.time_input('t', datetime.time(9, 30))\n"
        "c = st.color_picker('c', '#ABCDEF')\n"
        "st.write(f'{d.isoformat()}|{t.strftime(\"%H:%M\")}|{c}')\n"
    )
    at = AppTest.from_string(script)
    at.run()
    assert at.date_input[0].value == datetime.date(2023, 3, 14)
    assert at.time_input[0].value == datetime.time(9, 30)
    assert at.color_picker[0].value == "#ABCDEF"
    assert at.markdown[0].value == "2023-03-14|09:30|#ABCDEF"

    at.date_input[0].set_value(datetime.date(2024, 1, 1))
    at.time_input[0].set_value(datetime.time(18, 45))
    at.color_picker[0].set_value("#123456")
    at.run()
    assert at.markdown[0].value == "2024-01-01|18:45|#123456"


# --- Sidebar / tree structure ---------------------------------------------


def test_sidebar_is_separate_from_main():
    script = (
        "import streamlit as st\n"
        "st.write('main body')\n"
        "with st.sidebar:\n"
        "    st.write('side body')\n"
        "    st.button('side action')\n"
    )
    at = AppTest.from_string(script)
    at.run()
    assert [m.value for m in at.main.markdown] == ["main body"]
    assert [m.value for m in at.sidebar.markdown] == ["side body"]
    assert len(at.sidebar.button) == 1
    # No sidebar buttons on the main region, and no main-region buttons on
    # the sidebar side.
    assert len(at.main.button) == 0
    assert len(at.markdown) == 2


def test_sidebar_alerts_stay_in_sidebar():
    script = (
        "import streamlit as st\n"
        "st.info('main-info')\n"
        "with st.sidebar:\n"
        "    st.warning('side-warn')\n"
    )
    at = AppTest.from_string(script)
    at.run()
    assert [e.value for e in at.main.info] == ["main-info"]
    assert [e.value for e in at.main.warning] == []
    assert [e.value for e in at.sidebar.warning] == ["side-warn"]
    assert [e.value for e in at.sidebar.info] == []


def test_tree_navigation():
    script = "import streamlit as st\nst.write('one')\nst.write('two')\n"
    at = AppTest.from_string(script)
    at.run()
    # root has exactly the main block and the sidebar block
    assert len(at) == 2
    assert at[0].type == "main"
    assert at[1].type == "sidebar"
    assert len(at.main) == 2
    assert at.main[1].value == "two"
    assert len(at.get("markdown")) == 2
    assert len(at.get("button")) == 0
    assert at.get("markdown")[0].value == "one"


def test_get_unknown_element_type_is_empty():
    at = AppTest.from_string("import streamlit as st\nst.title('t')\n")
    at.run()
    result = at.get("no_such_element_type_exists")
    assert len(result) == 0
    # It must still be iterable/indexable — no crash.
    assert list(result) == []


# --- Session state ---------------------------------------------------------


def test_session_state_lifecycle():
    script = (
        "import streamlit as st\n"
        "if 'n' not in st.session_state:\n"
        "    st.session_state.n = 0\n"
        "st.session_state.n += 1\n"
        "st.write(f\"n={st.session_state.n} preset={st.session_state.get('preset', 'none')}\")\n"
    )
    at = AppTest.from_string(script)
    at.session_state["preset"] = "yes"
    at.run()
    assert at.session_state["n"] == 1
    assert at.markdown[0].value == "n=1 preset=yes"
    at.run()
    assert at.session_state["n"] == 2
    assert at.markdown[0].value == "n=2 preset=yes"
    # third rerun keeps growing the counter — session state truly persists.
    at.run()
    assert at.session_state["n"] == 3


def test_widget_key_binds_to_session_state():
    script = (
        "import streamlit as st\n"
        "st.text_input('who', key='who')\n"
        "st.write(st.session_state.who)\n"
    )
    at = AppTest.from_string(script)
    at.run()
    at.text_input[0].input("grace").run()
    assert at.session_state["who"] == "grace"
    assert at.markdown[0].value == "grace"


def test_query_params_passed_to_script():
    script = (
        "import streamlit as st\n"
        "p = st.experimental_get_query_params()\n"
        "st.write(p.get('q', ['none'])[0])\n"
    )
    at = AppTest.from_string(script)
    at.query_params["q"] = "answer"
    at.run()
    assert at.markdown[0].value == "answer"


# --- Exceptions & timeouts -------------------------------------------------


def test_script_exception_is_captured():
    script = (
        "import streamlit as st\n"
        "st.write('alive')\n"
        "raise ValueError('kaboom-42')\n"
    )
    at = AppTest.from_string(script)
    at.run()
    assert at.markdown[0].value == "alive"
    assert len(at.exception) == 1
    assert "kaboom-42" in at.exception[0].value
    assert "ValueError" in "".join(at.exception[0].stack_trace)


def test_undefined_name_surfaces_as_exception():
    script = (
        "import streamlit as st\n"
        "st.write('ok')\n"
        "totally_missing_name()\n"
    )
    at = AppTest.from_string(script)
    at.run()
    assert len(at.exception) == 1
    assert "totally_missing_name" in at.exception[0].message


def test_run_timeout_raises():
    script = (
        "import streamlit as st\n"
        "import time\n"
        "st.write('early')\n"
        "time.sleep(5)\n"
    )
    at = AppTest.from_string(script)
    with pytest.raises(RuntimeError):
        at.run(timeout=1)


# --- Cache stability -------------------------------------------------------


def test_cache_results_stable_across_reruns():
    script = (
        "import streamlit as st\n"
        "import uuid\n"
        "\n"
        "@st.cache_data\n"
        "def make_id():\n"
        "    return str(uuid.uuid4())\n"
        "\n"
        "st.write(make_id())\n"
    )
    at = AppTest.from_string(script)
    at.run()
    first = at.markdown[0].value
    at.run()
    assert at.markdown[0].value == first
    at.run()
    assert at.markdown[0].value == first


# --- Batched interactions --------------------------------------------------


def test_batched_interactions_apply_on_next_run():
    script = (
        "import streamlit as st\n"
        "name = st.text_input('name')\n"
        "agree = st.checkbox('agree')\n"
        "level = st.slider('level', 0, 10, 5)\n"
        "st.write(f'{name}|{agree}|{level}')\n"
    )
    at = AppTest.from_string(script)
    at.run()
    assert at.markdown[0].value == "|False|5"
    at.text_input[0].input("bob")
    at.checkbox[0].check()
    at.slider[0].set_value(9)
    at.run()
    assert at.markdown[0].value == "bob|True|9"


def test_missing_widget_types_yield_empty_lists():
    at = AppTest.from_string("import streamlit as st\nst.write('solo')\n")
    at.run()
    assert len(at.button) == 0
    assert len(at.slider) == 0
    assert len(at.text_input) == 0
    assert len(at.selectbox) == 0
    assert len(at.exception) == 0
    # New alert accessors return empty lists too when nothing was emitted.
    assert len(at.info) == 0
    assert len(at.warning) == 0
    assert len(at.error) == 0
    assert len(at.success) == 0


# --- Regression contract for the old ``InteractiveScriptTests`` entry point
#
# The compatibility promise is that the pre-existing class continues to work
# — no one's existing code should suddenly break — but that instantiating it
# emits a real ``DeprecationWarning`` (via ``warnings.warn``, so
# ``pytest.warns`` and ``-W error::DeprecationWarning`` can both see it),
# pointing users at ``AppTest``.


def test_old_interactive_script_tests_still_works_but_warns():
    from streamlit.testing.script_interactions import InteractiveScriptTests

    class _Tmp(InteractiveScriptTests):
        def runTest(self):  # pragma: no cover - satisfies unittest
            pass

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        instance = _Tmp()
        # It must still produce a usable dir_path attribute — the old API
        # contract — so any pre-existing subclass keeps behaving.
        assert hasattr(instance, "dir_path")

    dep_msgs = [
        str(w.message)
        for w in caught
        if issubclass(w.category, DeprecationWarning)
    ]
    assert dep_msgs, "InteractiveScriptTests must emit a DeprecationWarning"
    joined = " ".join(dep_msgs)
    assert "InteractiveScriptTests" in joined
    # The warning must actively steer users to the replacement API, not just
    # say "deprecated".
    assert "AppTest" in joined


def test_deprecation_warning_is_matchable_by_pytest_warns():
    from streamlit.testing.script_interactions import InteractiveScriptTests

    class _Tmp2(InteractiveScriptTests):
        def runTest(self):  # pragma: no cover
            pass

    with pytest.warns(DeprecationWarning, match=r"AppTest"):
        _Tmp2()


# --- Widget identity stability --------------------------------------------


def test_widget_id_stable_across_reruns():
    script = (
        "import streamlit as st\n"
        "st.slider('lvl', 0, 10, 5, key='lvl')\n"
    )
    at = AppTest.from_string(script)
    at.run()
    id_before = at.slider[0].id
    at.slider[0].set_value(7).run()
    id_after = at.slider[0].id
    assert id_before == id_after
    assert at.slider[0].value == 7
