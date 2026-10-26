"""Behavioral contract checks for the chart-specification linting feature.

These tests exercise only the public surface: ``altair.lint()``, the
``.lint()`` method on chart objects, the ``LintIssue`` value object, the
``LintError`` exception, and the ``strict_lint`` keyword the linter
must wire into ``to_dict`` / ``to_json``.  Every expectation (rule
code, severity string, path shape, exception class, valid value set)
is frozen inside this file; nothing is derived at runtime from the
state of the repository under test.
"""

from __future__ import annotations

import pytest

import altair as alt

DATA = {"values": [{"a": 1, "b": 2, "c": "x", "d": "2020-01-01"}]}


def lint(chart):
    return alt.lint(chart)


def codes(issues):
    return [issue.code for issue in issues]


def by_code(issues, code):
    return [issue for issue in issues if issue.code == code]


# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------


def test_lint_function_exists():
    assert callable(alt.lint)


def test_lint_issue_class_exists():
    assert isinstance(alt.LintIssue, type)


def test_lint_error_class_exists():
    assert isinstance(alt.LintError, type)
    assert issubclass(alt.LintError, ValueError)


def test_public_names_in_all():
    assert "lint" in alt.__all__
    assert "LintIssue" in alt.__all__
    assert "LintError" in alt.__all__


def test_clean_chart_returns_empty_list():
    chart = alt.Chart(DATA).mark_point().encode(x="a:Q", y="sum(b):Q")
    result = lint(chart)
    assert result == []
    assert isinstance(result, list)


def test_method_on_chart_matches_function():
    chart = alt.Chart(DATA).mark_bar().encode(y=alt.Y("b:Q", aggregate="wibble"))
    via_method = chart.lint()
    via_function = lint(chart)
    assert len(via_method) == len(via_function) == 1
    assert via_method[0].code == via_function[0].code == "E003"


@pytest.mark.parametrize(
    "build",
    [
        lambda: alt.Chart(DATA).mark_point().encode(x="a:Q"),
        lambda: alt.layer(alt.Chart(DATA).mark_point().encode(x="a:Q")),
        lambda: alt.concat(alt.Chart(DATA).mark_point().encode(x="a:Q")),
        lambda: alt.hconcat(alt.Chart(DATA).mark_point().encode(x="a:Q")),
        lambda: alt.vconcat(alt.Chart(DATA).mark_point().encode(x="a:Q")),
        lambda: alt.Chart(DATA).mark_point().encode(x="a:Q").facet("c:N"),
        lambda: alt.Chart(DATA)
        .mark_point()
        .encode(x=alt.X(alt.repeat("repeat"), type="quantitative"))
        .repeat(repeat=["a", "b"]),
    ],
)
def test_method_available_on_all_chart_types(build):
    assert build().lint() == []


def test_dict_spec_accepted():
    spec = {
        "mark": "point",
        "encoding": {"x": {"field": "a", "type": "quantitative"}},
    }
    assert lint(spec) == []


def test_unsupported_input_raises_type_error():
    with pytest.raises(TypeError):
        lint("not a chart")
    with pytest.raises(TypeError):
        lint(42)
    with pytest.raises(TypeError):
        lint(None)
    with pytest.raises(TypeError):
        lint(["mark"])


def test_issue_object_contract():
    chart = alt.Chart(DATA).mark_bar().encode(y=alt.Y("b:Q", aggregate="wibble"))
    (issue,) = lint(chart)
    assert issue.code == "E003"
    assert issue.severity == "error"
    assert issue.path == "encoding.y"
    assert isinstance(issue.message, str) and "wibble" in issue.message
    text = str(issue)
    assert "E003" in text and "error" in text and "encoding.y" in text


def test_issues_sorted_by_path_then_code():
    chart = alt.Chart(DATA).mark_point().encode(
        x2="b:Q", y=alt.Y("c:Q", aggregate="wibble")
    )
    issues = lint(chart)
    keys = [(issue.path, issue.code) for issue in issues]
    assert keys == sorted(keys)


def test_severity_matches_code_prefix():
    chart = alt.Chart(DATA).mark_area().encode(x2=alt.X2("b"))
    issues = lint(chart)
    assert issues
    for issue in issues:
        if issue.code.startswith("E"):
            assert issue.severity == "error"
        elif issue.code.startswith("W"):
            assert issue.severity == "warning"
        else:  # pragma: no cover - contract guard
            raise AssertionError(f"unexpected code {issue.code}")


# ---------------------------------------------------------------------------
# E001: unknown encoding channel
# ---------------------------------------------------------------------------


def test_e001_unknown_channel():
    spec = {
        "mark": "point",
        "encoding": {
            "x": {"field": "a", "type": "quantitative"},
            "colour": {"field": "c", "type": "nominal"},
        },
    }
    issues = by_code(lint(spec), "E001")
    assert len(issues) == 1
    assert issues[0].path == "encoding.colour"
    assert issues[0].severity == "error"
    assert "colour" in issues[0].message


def test_e001_known_channels_not_flagged():
    spec = {
        "mark": "point",
        "encoding": {
            "x": {"field": "a", "type": "quantitative"},
            "y": {"field": "b", "type": "quantitative"},
            "color": {"field": "c", "type": "nominal"},
            "tooltip": {"field": "d", "type": "temporal"},
            "xOffset": {"value": 3},
            "row": {"field": "c", "type": "nominal"},
            "column": {"field": "c", "type": "nominal"},
            "facet": {"field": "c", "type": "nominal"},
        },
    }
    assert by_code(lint(spec), "E001") == []


# ---------------------------------------------------------------------------
# E002: missing / unknown mark
# ---------------------------------------------------------------------------


def test_e002_unknown_mark():
    chart = alt.Chart(DATA).mark_point().encode(x="a:Q")
    chart.mark = "wibble"
    issues = by_code(lint(chart), "E002")
    assert len(issues) == 1
    assert issues[0].path == "mark"
    assert issues[0].severity == "error"
    assert "wibble" in issues[0].message


def test_e002_missing_mark():
    chart = alt.Chart(DATA).encode(x="a:Q")
    assert len(by_code(lint(chart), "E002")) == 1


def test_e002_known_marks_not_flagged():
    for mark in [
        "arc", "area", "bar", "image", "line", "point", "rect", "rule",
        "text", "tick", "trail", "circle", "square", "geoshape",
        "boxplot", "errorbar", "errorband",
    ]:
        spec = {
            "mark": mark,
            "encoding": {"x": {"field": "a", "type": "quantitative"}},
        }
        assert by_code(lint(spec), "E002") == [], mark


def test_e002_dict_form_mark():
    spec = {
        "mark": {"type": "point", "color": "red", "size": 100},
        "encoding": {"x": {"field": "a", "type": "quantitative"}},
    }
    assert by_code(lint(spec), "E002") == []


# ---------------------------------------------------------------------------
# E003: unknown aggregate
# ---------------------------------------------------------------------------


def test_e003_unknown_aggregate():
    chart = alt.Chart(DATA).mark_bar().encode(y=alt.Y("b:Q", aggregate="wibble"))
    issues = by_code(lint(chart), "E003")
    assert len(issues) == 1
    assert issues[0].path == "encoding.y"
    assert "wibble" in issues[0].message


def test_e003_frozen_valid_aggregates():
    valid = [
        "argmax", "argmin", "average", "count", "distinct", "max", "mean",
        "median", "min", "missing", "product", "q1", "q3", "ci0", "ci1",
        "stderr", "stdev", "stdevp", "sum", "valid", "values", "variance",
        "variancep", "exponential", "exponentialb",
    ]
    for op in valid:
        spec = {
            "mark": "bar",
            "encoding": {
                "y": {"field": "b", "type": "quantitative", "aggregate": op}
            },
        }
        assert by_code(lint(spec), "E003") == [], op


# ---------------------------------------------------------------------------
# E004: unknown timeUnit
# ---------------------------------------------------------------------------


def test_e004_unknown_timeunit():
    spec = {
        "mark": "line",
        "encoding": {
            "x": {"field": "d", "type": "temporal", "timeUnit": "fortnight"}
        },
    }
    issues = by_code(lint(spec), "E004")
    assert len(issues) == 1
    assert issues[0].path == "encoding.x"
    assert "fortnight" in issues[0].message


def test_e004_frozen_valid_timeunits():
    for unit in [
        "year", "quarter", "month", "week", "day", "dayofyear", "date",
        "hours", "minutes", "seconds", "milliseconds",
        "utcyear", "utcmonth", "utcmilliseconds",
        "yearmonth", "yearmonthdatehoursminutesseconds", "weekday",
        "utcyearweekdayhours", "utcsecondsmilliseconds",
        "binnedyear", "binnedutcyearmonthdate", "binnedyeardayofyear",
    ]:
        spec = {
            "mark": "line",
            "encoding": {"x": {"field": "d", "type": "temporal", "timeUnit": unit}},
        }
        assert by_code(lint(spec), "E004") == [], unit


# ---------------------------------------------------------------------------
# E005: unknown measurement type
# ---------------------------------------------------------------------------


def test_e005_unknown_type():
    spec = {"mark": "point", "encoding": {"x": {"field": "a", "type": "integer"}}}
    issues = by_code(lint(spec), "E005")
    assert len(issues) == 1
    assert "integer" in issues[0].message


def test_e005_valid_types():
    for field_type in ["quantitative", "ordinal", "temporal", "nominal", "geojson"]:
        spec = {"mark": "point", "encoding": {"x": {"field": "a", "type": field_type}}}
        assert by_code(lint(spec), "E005") == [], field_type


# ---------------------------------------------------------------------------
# E006 / E007: scale rules
# ---------------------------------------------------------------------------


def test_e006_unknown_scale_type():
    chart = alt.Chart(DATA).mark_point().encode(
        x=alt.X("a:Q", scale={"type": "hyperbolic"})
    )
    issues = by_code(lint(chart), "E006")
    assert len(issues) == 1
    assert issues[0].path == "encoding.x"
    assert "hyperbolic" in issues[0].message


def test_e006_frozen_valid_scale_types():
    for scale_type in [
        "linear", "log", "pow", "sqrt", "symlog", "identity", "sequential",
        "time", "utc", "quantile", "quantize", "threshold", "bin-ordinal",
        "ordinal", "point", "band",
    ]:
        spec = {
            "mark": "point",
            "encoding": {
                "x": {
                    "field": "a",
                    "type": "quantitative",
                    "scale": {"type": scale_type},
                }
            },
        }
        assert by_code(lint(spec), "E006") == [], scale_type


@pytest.mark.parametrize("scale_type", ["log", "pow", "sqrt", "symlog"])
@pytest.mark.parametrize("field_type", ["nominal", "ordinal", "geojson"])
def test_e007_continuous_scale_with_discrete_field(scale_type, field_type):
    spec = {
        "mark": "point",
        "encoding": {
            "x": {
                "field": "a",
                "type": field_type,
                "scale": {"type": scale_type},
            }
        },
    }
    issues = by_code(lint(spec), "E007")
    assert len(issues) == 1
    assert issues[0].path == "encoding.x"
    assert issues[0].severity == "error"


@pytest.mark.parametrize("scale_type", ["time", "utc"])
def test_e007_temporal_scale_requires_temporal(scale_type):
    spec = {
        "mark": "point",
        "encoding": {
            "x": {
                "field": "a",
                "type": "quantitative",
                "scale": {"type": scale_type},
            }
        },
    }
    assert len(by_code(lint(spec), "E007")) == 1


def test_e007_sequential_with_nominal():
    spec = {
        "mark": "point",
        "encoding": {
            "color": {
                "field": "c",
                "type": "nominal",
                "scale": {"type": "sequential"},
            }
        },
    }
    assert len(by_code(lint(spec), "E007")) == 1


def test_e007_sequential_with_ordinal_clean():
    spec = {
        "mark": "point",
        "encoding": {
            "color": {
                "field": "c",
                "type": "ordinal",
                "scale": {"type": "sequential"},
            }
        },
    }
    assert by_code(lint(spec), "E007") == []


def test_e007_compatible_combinations_clean():
    spec = {
        "mark": "point",
        "encoding": {
            "x": {"field": "a", "type": "quantitative", "scale": {"type": "log"}},
            "y": {"field": "d", "type": "temporal", "scale": {"type": "utc"}},
        },
    }
    assert by_code(lint(spec), "E007") == []


def test_e007_field_type_unknown_skipped():
    """When the field type is missing, E007 must not fire on its own."""
    spec = {
        "mark": "point",
        "encoding": {
            "x": {"field": "a", "scale": {"type": "log"}}
        },
    }
    assert by_code(lint(spec), "E007") == []


# ---------------------------------------------------------------------------
# E008: unknown stack value
# ---------------------------------------------------------------------------


def test_e008_unknown_stack():
    chart = alt.Chart(DATA).mark_bar().encode(y=alt.Y("b:Q", stack="sideways"))
    issues = by_code(lint(chart), "E008")
    assert len(issues) == 1
    assert issues[0].path == "encoding.y"
    assert "sideways" in issues[0].message


def test_e008_valid_stacks():
    for stack in [True, False, None, "zero", "center", "normalize"]:
        chart = alt.Chart(DATA).mark_bar().encode(y=alt.Y("b:Q", stack=stack))
        assert by_code(lint(chart), "E008") == [], stack


# ---------------------------------------------------------------------------
# E009: shorthand problems
# ---------------------------------------------------------------------------


def test_e009_bad_type_code():
    chart = alt.Chart(DATA).mark_point().encode(x="a:X")
    issues = by_code(lint(chart), "E009")
    assert len(issues) == 1
    assert issues[0].path == "encoding.x"
    assert issues[0].severity == "error"


def test_e009_shorthand_plus_field():
    spec = {
        "mark": "point",
        "encoding": {
            "x": {"shorthand": "a:Q", "field": "b", "type": "quantitative"}
        },
    }
    assert len(by_code(lint(spec), "E009")) == 1


def test_e009_in_list_valued_channel():
    chart = alt.Chart(DATA).mark_point().encode(x="a:Q", tooltip=["b:Q", "c:W"])
    issues = by_code(lint(chart), "E009")
    assert [issue.path for issue in issues] == ["encoding.tooltip[1]"]


def test_lint_reports_where_serialization_raises():
    chart = alt.Chart(DATA).mark_point().encode(x="a:X")
    with pytest.raises(Exception):
        chart.to_dict()
    assert by_code(lint(chart), "E009") != []


# ---------------------------------------------------------------------------
# E010: aggregate combined with bin
# ---------------------------------------------------------------------------


def test_e010_aggregate_with_bin():
    chart = alt.Chart(DATA).mark_bar().encode(
        x=alt.X("a:Q", bin=True, aggregate="sum")
    )
    issues = by_code(lint(chart), "E010")
    assert len(issues) == 1
    assert issues[0].path == "encoding.x"
    assert "sum" in issues[0].message


def test_e010_bin_without_aggregate_clean():
    chart = alt.Chart(DATA).mark_bar().encode(
        x=alt.X("a:Q", bin=True), y="count():Q"
    )
    assert by_code(lint(chart), "E010") == []


def test_e010_binned_string_does_not_trigger():
    """The literal string ``"binned"`` marks pre-binned data; E010 must not fire."""
    spec = {
        "mark": "bar",
        "encoding": {
            "x": {
                "field": "a",
                "type": "quantitative",
                "aggregate": "mean",
                "bin": "binned",
            }
        },
    }
    assert by_code(lint(spec), "E010") == []


# ---------------------------------------------------------------------------
# E011: unknown sort directive
# ---------------------------------------------------------------------------


def test_e011_unknown_sort():
    chart = alt.Chart(DATA).mark_bar().encode(x=alt.X("c:N", sort="sideways"))
    issues = by_code(lint(chart), "E011")
    assert len(issues) == 1
    assert issues[0].path == "encoding.x"
    assert "sideways" in issues[0].message


def test_e011_valid_sorts():
    for sort in ["ascending", "descending", "-y", "x", "color", "-size"]:
        spec = {
            "mark": "bar",
            "encoding": {"x": {"field": "c", "type": "nominal", "sort": sort}},
        }
        assert by_code(lint(spec), "E011") == [], sort


def test_e011_list_sort_not_flagged():
    """Non-string sort values (arrays, sort-field configs) never fire E011."""
    spec = {
        "mark": "bar",
        "encoding": {
            "x": {"field": "c", "type": "nominal", "sort": ["b", "a", "c"]},
        },
    }
    assert by_code(lint(spec), "E011") == []


# ---------------------------------------------------------------------------
# E012: duplicate parameter names
# ---------------------------------------------------------------------------


def test_e012_duplicate_params():
    spec = {
        "mark": "point",
        "params": [{"name": "brush"}, {"name": "brush"}],
        "encoding": {"x": {"field": "a", "type": "quantitative"}},
    }
    issues = by_code(lint(spec), "E012")
    assert len(issues) == 1
    assert issues[0].path == "params[1]"
    assert issues[0].severity == "error"
    assert "brush" in issues[0].message


def test_e012_duplicate_across_levels():
    spec = {
        "params": [{"name": "p"}],
        "layer": [
            {
                "mark": "point",
                "params": [{"name": "p"}],
                "encoding": {"x": {"field": "a", "type": "quantitative"}},
            }
        ],
    }
    issues = by_code(lint(spec), "E012")
    assert [issue.path for issue in issues] == ["layer[0].params[0]"]


def test_e012_unique_names_clean():
    spec = {
        "mark": "point",
        "params": [{"name": "p"}, {"name": "q"}],
        "encoding": {"x": {"field": "a", "type": "quantitative"}},
    }
    assert lint(spec) == []


# ---------------------------------------------------------------------------
# E013: invalid bin value
# ---------------------------------------------------------------------------


def test_e013_invalid_bin_string():
    chart = alt.Chart(DATA).mark_bar().encode(x=alt.X("a:Q", bin="yes"))
    issues = by_code(lint(chart), "E013")
    assert len(issues) == 1
    assert "yes" in issues[0].message


def test_e013_binned_clean():
    chart = alt.Chart(DATA).mark_bar().encode(x=alt.X("a:Q", bin="binned"))
    assert by_code(lint(chart), "E013") == []


# ---------------------------------------------------------------------------
# E014: channel without data reference
# ---------------------------------------------------------------------------


def test_e014_empty_channel():
    chart = alt.Chart(DATA).mark_point().encode(x="")
    issues = by_code(lint(chart), "E014")
    assert len(issues) == 1
    assert issues[0].path == "encoding.x"
    assert issues[0].severity == "error"


def test_e014_count_aggregate_exempt():
    chart = alt.Chart(DATA).mark_bar().encode(y="count():Q", x="c:N")
    assert lint(chart) == []


def test_e014_value_and_datum_exempt():
    chart = alt.Chart(DATA).mark_point().encode(
        x="a:Q", color=alt.value("red"), y=alt.datum(5)
    )
    assert by_code(lint(chart), "E014") == []


# ---------------------------------------------------------------------------
# W001: secondary channel without base
# ---------------------------------------------------------------------------


def test_w001_x2_without_x():
    chart = alt.Chart(DATA).mark_area().encode(x2="b:Q")
    issues = by_code(lint(chart), "W001")
    assert len(issues) == 1
    assert issues[0].path == "encoding.x2"
    assert issues[0].severity == "warning"


def test_w001_y2_without_y():
    chart = alt.Chart(DATA).mark_area().encode(y2="b:Q")
    assert [issue.path for issue in by_code(lint(chart), "W001")] == ["encoding.y2"]


@pytest.mark.parametrize(
    "secondary,base",
    [
        ("x2", "x"),
        ("y2", "y"),
        ("longitude2", "longitude"),
        ("latitude2", "latitude"),
    ],
)
def test_w001_secondary_pairs(secondary, base):
    spec = {"mark": "point", "encoding": {secondary: {"value": 3}}}
    issues = by_code(lint(spec), "W001")
    assert [i.path for i in issues] == [f"encoding.{secondary}"]

    spec_ok = {
        "mark": "point",
        "encoding": {
            secondary: {"value": 3},
            base: {"field": "a", "type": "quantitative"},
        },
    }
    assert by_code(lint(spec_ok), "W001") == []


def test_w001_with_base_channel_clean():
    chart = alt.Chart(DATA).mark_area().encode(x="a:Q", x2="b:Q")
    assert by_code(lint(chart), "W001") == []


# ---------------------------------------------------------------------------
# W002: empty unit chart
# ---------------------------------------------------------------------------


def test_w002_empty_chart():
    chart = alt.Chart(DATA).mark_point()
    issues = by_code(lint(chart), "W002")
    assert len(issues) == 1
    assert issues[0].path == ""
    assert issues[0].severity == "warning"


def test_w002_geoshape_exempt():
    assert lint(alt.Chart(DATA).mark_geoshape()) == []


def test_w002_shared_layer_encoding_exempt():
    chart = alt.layer(
        alt.Chart(DATA).mark_point(),
        alt.Chart(DATA).mark_line(),
    ).encode(x="a:Q", y="b:Q")
    assert lint(chart) == []


# ---------------------------------------------------------------------------
# W003: missing explicit type
# ---------------------------------------------------------------------------


def test_w003_field_without_type():
    chart = alt.Chart(DATA).mark_point().encode(x="a")
    issues = by_code(lint(chart), "W003")
    assert len(issues) == 1
    assert issues[0].path == "encoding.x"
    assert issues[0].severity == "warning"


# ---------------------------------------------------------------------------
# Nested traversal paths
# ---------------------------------------------------------------------------


def test_nested_layer_paths():
    spec = {
        "layer": [
            {"mark": "point", "encoding": {"x": {"field": "a", "type": "integer"}}},
            {"mark": "line", "encoding": {"y": {"field": "b", "type": "integer"}}},
        ]
    }
    issues = by_code(lint(spec), "E005")
    assert [issue.path for issue in issues] == [
        "layer[0].encoding.x",
        "layer[1].encoding.y",
    ]


def test_nested_concat_layer_path():
    chart = alt.concat(
        alt.Chart(DATA).mark_point().encode(x="a:Q"),
        alt.layer(
            alt.Chart(DATA).mark_point().encode(y=alt.Y("b:Q", aggregate="wibble"))
        ),
    )
    issues = by_code(lint(chart), "E003")
    assert [issue.path for issue in issues] == ["concat[1].layer[0].encoding.y"]


def test_facet_spec_and_channel_paths():
    spec = {
        "facet": {"column": {"field": "c", "type": "nominal", "sort": "sideways"}},
        "spec": {
            "mark": "point",
            "encoding": {"x": {"field": "a", "type": "integer"}},
        },
    }
    issues = lint(spec)
    assert [issue.path for issue in by_code(issues, "E011")] == ["facet.column"]
    assert [issue.path for issue in by_code(issues, "E005")] == ["spec.encoding.x"]


def test_facet_shorthand_string_path():
    chart = alt.Chart(DATA).mark_point().encode(x="a:Q").facet("c:X")
    issues = by_code(lint(chart), "E009")
    assert [issue.path for issue in issues] == ["facet"]


def test_hconcat_path():
    spec = {
        "hconcat": [
            {"mark": "point", "encoding": {"x": {"field": "a", "type": "integer"}}}
        ]
    }
    issues = by_code(lint(spec), "E005")
    assert [issue.path for issue in issues] == ["hconcat[0].encoding.x"]


def test_input_not_mutated():
    chart = alt.Chart(DATA).mark_point().encode(y=alt.Y("b:Q", aggregate="wibble"))
    before = repr(chart)
    lint(chart)
    assert repr(chart) == before


def test_repeatable_results():
    chart = alt.Chart(DATA).mark_point().encode(y=alt.Y("b:Q", aggregate="wibble"))
    first = lint(chart)
    second = lint(chart)
    assert [(i.code, i.severity, i.path, i.message) for i in first] == [
        (i.code, i.severity, i.path, i.message) for i in second
    ]


def test_lint_does_not_break_subsequent_to_dict():
    chart = alt.Chart(DATA).mark_point().encode(x="a:Q", y="sum(b):Q")
    lint(chart)  # first pass -- must not mutate
    # to_dict must still succeed and produce the expected mark type
    d = chart.to_dict()
    assert d["mark"] == {"type": "point"}


# ---------------------------------------------------------------------------
# LintError container
# ---------------------------------------------------------------------------


def test_lint_error_is_value_error():
    err = alt.LintError([alt.LintIssue("E001", "error", "encoding.x", "boom")])
    assert isinstance(err, ValueError)
    assert isinstance(err, alt.LintError)


def test_lint_error_carries_issues():
    a = alt.LintIssue("E003", "error", "encoding.y", "bad agg")
    b = alt.LintIssue("E001", "error", "encoding.colour", "unknown channel")
    err = alt.LintError([a, b])
    assert list(err.issues) == [a, b]
    text = str(err)
    assert "E003" in text and "E001" in text
    assert "encoding.y" in text and "encoding.colour" in text


def test_lint_error_str_reports_count():
    err_one = alt.LintError([alt.LintIssue("E001", "error", "x", "one")])
    err_many = alt.LintError(
        [
            alt.LintIssue("E001", "error", "x", "one"),
            alt.LintIssue("E002", "error", "y", "two"),
        ]
    )
    assert "1 error" in str(err_one)
    assert "2 error" in str(err_many)


# ---------------------------------------------------------------------------
# strict_lint keyword on to_dict / to_json
# ---------------------------------------------------------------------------


class TestStrictLintToDict:
    def test_default_preserves_clean_behavior(self):
        chart = alt.Chart(DATA).mark_point().encode(x="a:Q", y="sum(b):Q")
        default = chart.to_dict()
        explicit = chart.to_dict(strict_lint=False)
        assert default["mark"] == explicit["mark"] == {"type": "point"}

    def test_strict_lint_clean_chart_succeeds(self):
        chart = alt.Chart(DATA).mark_point().encode(x="a:Q", y="sum(b):Q")
        result = chart.to_dict(strict_lint=True)
        assert result["mark"] == {"type": "point"}

    def test_strict_lint_raises_on_error(self):
        chart = alt.Chart(DATA).mark_bar().encode(
            y=alt.Y("b:Q", aggregate="wibble")
        )
        with pytest.raises(alt.LintError) as info:
            chart.to_dict(strict_lint=True)
        assert [i.code for i in info.value.issues] == ["E003"]
        assert isinstance(info.value, ValueError)

    def test_strict_lint_warning_only_does_not_raise(self):
        chart = alt.Chart(DATA).mark_point()  # W002
        result = chart.to_dict(strict_lint=True)
        assert result["mark"] == {"type": "point"}

    def test_strict_lint_reports_from_inside_layer(self):
        chart = alt.layer(
            alt.Chart(DATA).mark_bar().encode(
                y=alt.Y("b:Q", aggregate="wibble")
            )
        )
        with pytest.raises(alt.LintError) as info:
            chart.to_dict(strict_lint=True)
        assert [(i.code, i.path) for i in info.value.issues] == [
            ("E003", "layer[0].encoding.y")
        ]

    def test_strict_lint_reports_from_inside_concat(self):
        chart = alt.concat(
            alt.Chart(DATA).mark_point().encode(x="a:Q"),
            alt.Chart(DATA).mark_bar().encode(
                y=alt.Y("b:Q", aggregate="wibble")
            ),
        )
        with pytest.raises(alt.LintError) as info:
            chart.to_dict(strict_lint=True)
        assert [(i.code, i.path) for i in info.value.issues] == [
            ("E003", "concat[1].encoding.y")
        ]

    def test_strict_lint_reports_from_inside_facet_spec(self):
        chart = alt.Chart(DATA).mark_bar().encode(
            y=alt.Y("b:Q", aggregate="wibble")
        ).facet("c:N")
        with pytest.raises(alt.LintError) as info:
            chart.to_dict(strict_lint=True)
        assert [(i.code, i.path) for i in info.value.issues] == [
            ("E003", "spec.encoding.y")
        ]

    def test_strict_lint_default_does_not_run_lint(self):
        """Without strict_lint, a chart that only trips W002 (warning) must
        still produce a valid dict; this proves default is not accidentally
        turning warnings into failures."""
        chart = alt.Chart(DATA).mark_point()
        assert "mark" in chart.to_dict()


class TestStrictLintToJson:
    def test_clean_chart_yields_string(self):
        chart = alt.Chart(DATA).mark_point().encode(x="a:Q", y="sum(b):Q")
        text = chart.to_json(strict_lint=True)
        assert isinstance(text, str) and '"mark"' in text

    def test_raises_on_error(self):
        chart = alt.Chart(DATA).mark_bar().encode(
            y=alt.Y("b:Q", aggregate="wibble")
        )
        with pytest.raises(alt.LintError):
            chart.to_json(strict_lint=True)

    def test_default_behavior_unchanged(self):
        """A clean chart's ``to_json()`` still round-trips."""
        chart = alt.Chart(DATA).mark_point().encode(x="a:Q", y="sum(b):Q")
        text = chart.to_json()
        assert '"mark"' in text


# ---------------------------------------------------------------------------
# Regression: existing behavior of parse_shorthand / to_dict must not change
# ---------------------------------------------------------------------------


def test_regression_parse_shorthand_field_only():
    """The existing docstring example must keep working."""
    assert alt.parse_shorthand("name") == {"field": "name"}


def test_regression_parse_shorthand_with_type():
    assert alt.parse_shorthand("name:Q") == {
        "field": "name",
        "type": "quantitative",
    }


def test_regression_parse_shorthand_aggregate():
    assert alt.parse_shorthand("average(col)") == {
        "aggregate": "average",
        "field": "col",
    }


def test_regression_parse_shorthand_time_unit():
    assert alt.parse_shorthand("month(col)") == {
        "field": "col",
        "timeUnit": "month",
        "type": "temporal",
    }


def test_regression_parse_shorthand_count():
    assert alt.parse_shorthand("count()") == {
        "aggregate": "count",
        "type": "quantitative",
    }


def test_regression_parse_shorthand_bad_type_still_raises():
    with pytest.raises(ValueError):
        alt.parse_shorthand("a:X")


def test_regression_chart_to_dict_unchanged():
    chart = alt.Chart(DATA).mark_point().encode(x="a:Q", y="sum(b):Q")
    d = chart.to_dict()
    assert d["mark"] == {"type": "point"}
    assert "encoding" in d


def test_regression_layer_to_dict_unchanged():
    a = alt.Chart(DATA).mark_point().encode(x="a:Q")
    b = alt.Chart(DATA).mark_line().encode(x="a:Q")
    d = alt.layer(a, b).to_dict()
    assert "layer" in d
    assert len(d["layer"]) == 2
