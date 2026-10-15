"""Verifier tests: per-instance training windows through the cutoff time
DataFrame, plus numeric training windows on entitysets with a numeric time
index, plus backward-compatibility regression checks.

All expected values below are frozen golden data computed from first
principles on fixed toy entitysets; nothing is recomputed from repository
state at verification time.

Window semantics (matching the pre-existing global training_window behavior):
with include_cutoff_time=True, a row of data at time t is used for a cutoff
time T with window W iff  T - W < t <= T ; with include_cutoff_time=False the
condition is  T - W <= t < T .
"""
import pandas as pd
import pytest

import featuretools as ft
from featuretools.feature_base import IdentityFeature
from featuretools.primitives import Count, Max, Mean, Min, Sum


# ---------------------------------------------------------------------------
# toy entitysets (fixed data, goldens below are hand-computed from these)
# ---------------------------------------------------------------------------

def make_shop_es():
    """customers 1/2/3 with events (time, amount, category):

    customer 1: 01-01(10,a) 01-05(20,a) 01-10(40,b) 01-20(80,b) 01-21(25,a)
    customer 2: 01-02(5,a) 01-08(15,b) 01-15(30,b) 01-22(35,b)
    customer 3: 01-19(7,a)
    """
    customers = pd.DataFrame(
        {
            "customer_id": [1, 2, 3],
            "age": [30, 40, 50],
            "region": ["north", "south", "north"],
        },
    )
    events = pd.DataFrame(
        {
            "event_id": range(10),
            "customer_id": [1, 1, 1, 1, 2, 2, 2, 3, 1, 2],
            "time": pd.to_datetime(
                [
                    "2023-01-01", "2023-01-05", "2023-01-10", "2023-01-20",
                    "2023-01-02", "2023-01-08", "2023-01-15", "2023-01-19",
                    "2023-01-21", "2023-01-22",
                ],
            ),
            "amount": [10.0, 20.0, 40.0, 80.0, 5.0, 15.0, 30.0, 7.0, 25.0, 35.0],
            "category": ["a", "a", "b", "b", "a", "b", "b", "a", "a", "b"],
        },
    )
    es = ft.EntitySet("shop")
    es.add_dataframe(customers, dataframe_name="customers", index="customer_id")
    es.add_dataframe(
        events, dataframe_name="events", index="event_id", time_index="time",
    )
    es.add_relationship("customers", "customer_id", "events", "customer_id")
    return es


def make_numeric_es():
    """device 1: logs at t=10(v=1), t=30(v=2), t=45(v=3)
    device 2: logs at t=5(v=4), t=25(v=5), t=49(v=6)
    """
    devices = pd.DataFrame({"device_id": [1, 2]})
    logs = pd.DataFrame(
        {
            "log_id": range(6),
            "device_id": [1, 1, 1, 2, 2, 2],
            "t": [10, 30, 45, 5, 25, 49],
            "v": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        },
    )
    es = ft.EntitySet("numeric_shop")
    es.add_dataframe(devices, dataframe_name="devices", index="device_id")
    es.add_dataframe(logs, dataframe_name="logs", index="log_id", time_index="t")
    es.add_relationship("devices", "device_id", "logs", "device_id")
    return es


def make_multihop_es():
    """customers <- sessions <- events.

    sessions: s1(c1, 01-03) s2(c1, 01-18) s3(c2, 01-06) s4(c2, 01-19)
    events: e1(s1, 01-04, 10) e2(s1, 01-10, 20) e3(s2, 01-19, 40)
            e4(s2, 01-20, 80) e5(s3, 01-07, 5) e6(s4, 01-21, 15)
    """
    customers = pd.DataFrame({"customer_id": [1, 2]})
    sessions = pd.DataFrame(
        {
            "session_id": ["s1", "s2", "s3", "s4"],
            "customer_id": [1, 1, 2, 2],
            "session_start": pd.to_datetime(
                ["2023-01-03", "2023-01-18", "2023-01-06", "2023-01-19"],
            ),
            "end_date": pd.to_datetime(
                ["2023-01-05", "2023-01-20", "2023-01-08", "2023-01-25"],
            ),
            "end_reason": ["done", "timeout", "done", None],
        },
    )
    events = pd.DataFrame(
        {
            "event_id": ["e1", "e2", "e3", "e4", "e5", "e6"],
            "session_id": ["s1", "s1", "s2", "s2", "s3", "s4"],
            "time": pd.to_datetime(
                [
                    "2023-01-04", "2023-01-10", "2023-01-19",
                    "2023-01-20", "2023-01-07", "2023-01-21",
                ],
            ),
            "amount": [10.0, 20.0, 40.0, 80.0, 5.0, 15.0],
        },
    )
    es = ft.EntitySet("multihop")
    es.add_dataframe(customers, dataframe_name="customers", index="customer_id")
    es.add_dataframe(
        sessions, dataframe_name="sessions", index="session_id",
        time_index="session_start",
    )
    es.add_dataframe(
        events, dataframe_name="events", index="event_id", time_index="time",
    )
    es.add_relationship("customers", "customer_id", "sessions", "customer_id")
    es.add_relationship("sessions", "session_id", "events", "session_id")
    return es


def shop_features(es):
    return [
        ft.Feature(
            es["events"].ww["event_id"],
            parent_dataframe_name="customers", primitive=Count,
        ),
        ft.Feature(
            es["events"].ww["amount"],
            parent_dataframe_name="customers", primitive=Sum,
        ),
        ft.Feature(
            es["events"].ww["amount"],
            parent_dataframe_name="customers", primitive=Mean,
        ),
        ft.Feature(
            es["events"].ww["amount"],
            parent_dataframe_name="customers", primitive=Max,
        ),
        ft.Feature(
            es["events"].ww["amount"],
            parent_dataframe_name="customers", primitive=Min,
        ),
    ]


def numeric_features(es):
    return [
        ft.Feature(
            es["logs"].ww["log_id"],
            parent_dataframe_name="devices", primitive=Count,
        ),
        ft.Feature(
            es["logs"].ww["v"],
            parent_dataframe_name="devices", primitive=Sum,
        ),
    ]


def windowed_cutoff(times, windows, extra=None):
    cutoff = pd.DataFrame(
        {
            "instance_id": [row[0] for row in times],
            "time": pd.to_datetime([row[1] for row in times]),
            "training_window": windows,
        },
    )
    for column, values in (extra or {}).items():
        cutoff[column] = values
    return cutoff


# ---------------------------------------------------------------------------
# core per-instance window behavior (fail on the pristine repository)
# ---------------------------------------------------------------------------

class TestPerInstanceWindowDatetime:
    def test_basic_values(self):
        es = make_shop_es()
        cutoff = windowed_cutoff(
            times=[
                (1, "2023-01-21"), (1, "2023-01-22"),
                (2, "2023-01-21"), (3, "2023-01-21"),
            ],
            windows=[
                pd.Timedelta("7d"), pd.Timedelta("16d"),
                pd.Timedelta("7d"), pd.Timedelta("7d"),
            ],
        )
        fm = ft.calculate_feature_matrix(
            shop_features(es), entityset=es, cutoff_time=cutoff,
        )
        # c1 @ 01-21, 7d -> (01-14, 01-21] -> events 01-20, 01-21
        assert fm["COUNT(events)"].iloc[0] == 2
        assert fm["SUM(events.amount)"].iloc[0] == 105.0
        assert fm["MEAN(events.amount)"].iloc[0] == 52.5
        assert fm["MAX(events.amount)"].iloc[0] == 80.0
        assert fm["MIN(events.amount)"].iloc[0] == 25.0
        # c1 @ 01-22, 16d -> (01-06, 01-22] -> 01-10, 01-20, 01-21
        assert fm["COUNT(events)"].iloc[1] == 3
        assert fm["SUM(events.amount)"].iloc[1] == 145.0
        # c2 @ 01-21, 7d -> (01-14, 01-21] -> 01-15
        assert fm["COUNT(events)"].iloc[2] == 1
        assert fm["SUM(events.amount)"].iloc[2] == 30.0
        # c3 @ 01-21, 7d -> 01-19
        assert fm["COUNT(events)"].iloc[3] == 1
        assert fm["SUM(events.amount)"].iloc[3] == 7.0

    def test_lower_edge_exclusive(self):
        es = make_shop_es()
        cutoff = windowed_cutoff(
            times=[(1, "2023-01-21")], windows=[pd.Timedelta("20d")],
        )
        fm = ft.calculate_feature_matrix(
            shop_features(es), entityset=es, cutoff_time=cutoff,
        )
        # (01-01, 01-21] excludes the event exactly 20 days old
        assert fm["COUNT(events)"].iloc[0] == 4
        assert fm["SUM(events.amount)"].iloc[0] == 165.0

    def test_missing_window_means_full_history(self):
        es = make_shop_es()
        cutoff = windowed_cutoff(
            times=[(1, "2023-01-21"), (2, "2023-01-21")],
            windows=[pd.NaT, pd.NaT],
        )
        fm = ft.calculate_feature_matrix(
            shop_features(es), entityset=es, cutoff_time=cutoff,
        )
        assert fm["COUNT(events)"].tolist() == [5, 3]
        assert fm["SUM(events.amount)"].tolist() == [175.0, 50.0]

    def test_mixed_null_and_value(self):
        es = make_shop_es()
        cutoff = windowed_cutoff(
            times=[(1, "2023-01-21"), (2, "2023-01-21"), (3, "2023-01-21")],
            windows=[pd.Timedelta("7d"), pd.NaT, pd.Timedelta("1d")],
        )
        fm = ft.calculate_feature_matrix(
            shop_features(es), entityset=es, cutoff_time=cutoff,
        )
        assert fm["COUNT(events)"].tolist() == [2, 3, 0]
        assert fm["SUM(events.amount)"].iloc[0] == 105.0
        assert fm["SUM(events.amount)"].iloc[1] == 50.0
        # empty window: Sum falls back to its default 0, Mean stays null
        assert fm["SUM(events.amount)"].iloc[2] == 0.0
        assert pd.isna(fm["MEAN(events.amount)"].iloc[2])

    def test_window_column_is_consumed_not_passed_through(self):
        es = make_shop_es()
        cutoff = windowed_cutoff(
            times=[(1, "2023-01-21"), (2, "2023-01-21")],
            windows=[pd.Timedelta("7d"), pd.NaT],
            extra={"label": ["x", "y"]},
        )
        fm = ft.calculate_feature_matrix(
            shop_features(es), entityset=es, cutoff_time=cutoff,
        )
        assert "training_window" not in fm.columns
        assert fm["label"].tolist() == ["x", "y"]

    def test_output_row_order_matches_cutoff_order(self):
        es = make_shop_es()
        cutoff = windowed_cutoff(
            times=[
                (3, "2023-01-21"), (1, "2023-01-22"),
                (2, "2023-01-21"), (1, "2023-01-21"),
            ],
            windows=[
                pd.Timedelta("7d"), pd.Timedelta("16d"),
                pd.NaT, pd.Timedelta("7d"),
            ],
            extra={"marker": [10, 11, 12, 13]},
        )
        fm = ft.calculate_feature_matrix(
            shop_features(es), entityset=es, cutoff_time=cutoff,
        )
        assert fm["marker"].tolist() == [10, 11, 12, 13]
        assert fm["COUNT(events)"].tolist() == [1, 3, 3, 2]

    def test_chunk_size_invariance(self):
        es = make_shop_es()
        cutoff = windowed_cutoff(
            times=[
                (1, "2023-01-21"), (1, "2023-01-22"),
                (2, "2023-01-21"), (3, "2023-01-21"),
            ],
            windows=[
                pd.Timedelta("7d"), pd.Timedelta("16d"),
                pd.NaT, pd.Timedelta("7d"),
            ],
        )
        full = ft.calculate_feature_matrix(
            shop_features(es), entityset=es, cutoff_time=cutoff,
        )
        assert full["COUNT(events)"].tolist() == [2, 3, 3, 1]
        for chunk_size in [1, 2, 0.5]:
            chunked = ft.calculate_feature_matrix(
                shop_features(es), entityset=es, cutoff_time=cutoff,
                chunk_size=chunk_size,
            )
            pd.testing.assert_frame_equal(full, chunked)

    def test_include_cutoff_time_false(self):
        es = make_shop_es()
        cutoff = windowed_cutoff(
            times=[(1, "2023-01-21")], windows=[pd.Timedelta("7d")],
        )
        fm = ft.calculate_feature_matrix(
            shop_features(es), entityset=es, cutoff_time=cutoff,
            include_cutoff_time=False,
        )
        # [01-14, 01-21) -> only 01-20
        assert fm["COUNT(events)"].iloc[0] == 1
        assert fm["SUM(events.amount)"].iloc[0] == 80.0

    def test_include_cutoff_time_false_window_edge(self):
        es = make_shop_es()
        cutoff = windowed_cutoff(
            times=[(1, "2023-01-21")], windows=[pd.Timedelta("20d")],
        )
        fm = ft.calculate_feature_matrix(
            shop_features(es), entityset=es, cutoff_time=cutoff,
            include_cutoff_time=False,
        )
        # [01-01, 01-21) -> 01-01, 01-05, 01-10, 01-20
        assert fm["COUNT(events)"].iloc[0] == 4
        assert fm["SUM(events.amount)"].iloc[0] == 150.0

    def test_zero_window_excludes_all_data(self):
        es = make_shop_es()
        cutoff = windowed_cutoff(
            times=[(1, "2023-01-21")], windows=[pd.Timedelta(0)],
        )
        fm = ft.calculate_feature_matrix(
            shop_features(es), entityset=es, cutoff_time=cutoff,
        )
        assert fm["COUNT(events)"].iloc[0] == 0
        assert fm["SUM(events.amount)"].iloc[0] == 0.0

    def test_where_features_respect_window(self):
        es = make_shop_es()
        where_feature = ft.Feature(
            es["events"].ww["event_id"],
            parent_dataframe_name="customers", primitive=Count,
            where=IdentityFeature(es["events"].ww["category"]) == "a",
        )
        cutoff = windowed_cutoff(
            times=[(1, "2023-01-21"), (1, "2023-01-22")],
            windows=[pd.Timedelta("2d"), pd.NaT],
        )
        fm = ft.calculate_feature_matrix(
            [where_feature], entityset=es, cutoff_time=cutoff,
        )
        # (01-19, 01-21] -> events 01-20(b), 01-21(a) -> one "a"
        assert fm.iloc[0, 0] == 1
        # full history -> "a" on 01-01, 01-05, 01-21
        assert fm.iloc[1, 0] == 3

    def test_target_identity_features_unaffected(self):
        es = make_shop_es()
        age = ft.Feature(es["customers"].ww["age"])
        count = ft.Feature(
            es["events"].ww["event_id"],
            parent_dataframe_name="customers", primitive=Count,
        )
        cutoff = windowed_cutoff(
            times=[(1, "2023-01-21"), (2, "2023-01-21")],
            windows=[pd.Timedelta("7d"), pd.Timedelta("7d")],
        )
        fm = ft.calculate_feature_matrix([age, count], entityset=es, cutoff_time=cutoff)
        assert fm["age"].tolist() == [30, 40]
        assert fm["COUNT(events)"].tolist() == [2, 1]

    def test_cutoff_time_in_index(self):
        es = make_shop_es()
        cutoff = windowed_cutoff(
            times=[(1, "2023-01-21"), (2, "2023-01-21")],
            windows=[pd.Timedelta("7d"), pd.NaT],
        )
        fm = ft.calculate_feature_matrix(
            shop_features(es), entityset=es, cutoff_time=cutoff,
            cutoff_time_in_index=True,
        )
        assert fm.index.names == ["customer_id", "time"]
        assert fm.index[0] == (1, pd.Timestamp("2023-01-21"))
        assert fm.index[1] == (2, pd.Timestamp("2023-01-21"))
        assert fm["COUNT(events)"].tolist() == [2, 3]

    def test_dfs_end_to_end(self):
        es = make_shop_es()
        cutoff = windowed_cutoff(
            times=[(1, "2023-01-21"), (2, "2023-01-21"), (3, "2023-01-21")],
            windows=[pd.Timedelta("7d"), pd.NaT, pd.Timedelta("1d")],
        )
        fm, _ = ft.dfs(
            entityset=es, target_dataframe_name="customers",
            cutoff_time=cutoff,
            agg_primitives=["count", "sum"], trans_primitives=[], max_depth=1,
        )
        assert "training_window" not in fm.columns
        assert fm["COUNT(events)"].tolist() == [2, 3, 0]
        assert fm["SUM(events.amount)"].tolist() == [105.0, 50.0, 0.0]

    def test_multihop_window_applies_to_every_dataframe_on_path(self):
        es = make_multihop_es()
        features = [
            ft.Feature(
                es["events"].ww["event_id"],
                parent_dataframe_name="customers", primitive=Count,
            ),
            ft.Feature(
                es["events"].ww["amount"],
                parent_dataframe_name="customers", primitive=Sum,
            ),
            ft.Feature(
                es["sessions"].ww["session_id"],
                parent_dataframe_name="customers", primitive=Count,
            ),
        ]
        cutoff = windowed_cutoff(
            times=[
                (1, "2023-01-21"), (1, "2023-01-22"),
                (2, "2023-01-21"), (2, "2023-01-22"),
            ],
            windows=[
                pd.Timedelta("7d"), pd.Timedelta("15d"),
                pd.Timedelta("7d"), pd.NaT,
            ],
        )
        fm = ft.calculate_feature_matrix(features, entityset=es, cutoff_time=cutoff)
        # c1 7d @ 01-21: events e3,e4 -> 2 / 120; sessions s2 -> 1
        assert fm["COUNT(events)"].iloc[0] == 2
        assert fm["SUM(events.amount)"].iloc[0] == 120.0
        assert fm["COUNT(sessions)"].iloc[0] == 1
        # c1 15d @ 01-22: session s1 filtered out so e1/e2 cannot join
        # through; events e3,e4 -> 2 / 120; sessions s2 -> 1
        assert fm["COUNT(events)"].iloc[1] == 2
        assert fm["SUM(events.amount)"].iloc[1] == 120.0
        assert fm["COUNT(sessions)"].iloc[1] == 1
        # c2 7d @ 01-21: e6 -> 1 / 15; s4 -> 1
        assert fm["COUNT(events)"].iloc[2] == 1
        assert fm["SUM(events.amount)"].iloc[2] == 15.0
        assert fm["COUNT(sessions)"].iloc[2] == 1
        # c2 full @ 01-22: e5,e6 -> 2 / 20; s3,s4 -> 2
        assert fm["COUNT(events)"].iloc[3] == 2
        assert fm["SUM(events.amount)"].iloc[3] == 20.0
        assert fm["COUNT(sessions)"].iloc[3] == 2

    def test_secondary_time_index_behavior_preserved(self):
        es = make_multihop_es()
        es.set_secondary_time_index(
            dataframe_name="sessions",
            secondary_time_index={"end_date": ["end_reason"]},
        )
        end_reason = ft.Feature(es["sessions"].ww["end_reason"])
        cutoff = windowed_cutoff(
            times=[("s2", "2023-01-19"), ("s2", "2023-01-21")],
            windows=[pd.Timedelta("7d"), pd.Timedelta("7d")],
        )
        fm = ft.calculate_feature_matrix([end_reason], entityset=es, cutoff_time=cutoff)
        assert pd.isna(fm["end_reason"].iloc[0])
        assert fm["end_reason"].iloc[1] == "timeout"

    def test_constant_column_matches_global_window(self):
        es = make_shop_es()
        features = shop_features(es)
        base_cutoff = pd.DataFrame(
            {
                "instance_id": [1, 2, 3, 1],
                "time": pd.to_datetime(
                    ["2023-01-21", "2023-01-21", "2023-01-21", "2023-01-22"],
                ),
            },
        )
        windowed = base_cutoff.copy()
        windowed["training_window"] = [pd.Timedelta("15d")] * 4
        expected = ft.calculate_feature_matrix(
            features, entityset=es, cutoff_time=base_cutoff,
            training_window="15 days",
        )
        actual = ft.calculate_feature_matrix(
            features, entityset=es, cutoff_time=windowed,
        )
        pd.testing.assert_frame_equal(expected, actual)

    def test_all_null_column_matches_no_column(self):
        es = make_shop_es()
        features = shop_features(es)
        base_cutoff = pd.DataFrame(
            {
                "instance_id": [1, 2, 3],
                "time": pd.to_datetime(["2023-01-21"] * 3),
            },
        )
        windowed = base_cutoff.copy()
        windowed["training_window"] = pd.NaT
        expected = ft.calculate_feature_matrix(
            features, entityset=es, cutoff_time=base_cutoff,
        )
        actual = ft.calculate_feature_matrix(
            features, entityset=es, cutoff_time=windowed,
        )
        pd.testing.assert_frame_equal(expected, actual)

    def test_rows_match_rowwise_global_calculations(self):
        es = make_shop_es()
        features = shop_features(es)
        rows = []
        windows = []
        for customer_id in [1, 2, 3]:
            for day, window in [
                ("2023-01-20", pd.Timedelta("3d")),
                ("2023-01-21", pd.Timedelta("7d")),
                ("2023-01-22", pd.NaT),
                ("2023-01-23", pd.Timedelta("14d")),
            ]:
                rows.append((customer_id, day))
                windows.append(window)
        cutoff = windowed_cutoff(times=rows, windows=windows)
        fm = ft.calculate_feature_matrix(features, entityset=es, cutoff_time=cutoff)
        assert len(fm) == 12
        for position, ((customer_id, day), window) in enumerate(zip(rows, windows)):
            expected = ft.calculate_feature_matrix(
                features, entityset=es,
                cutoff_time=pd.DataFrame(
                    {"instance_id": [customer_id], "time": [pd.Timestamp(day)]},
                ),
                training_window=None if pd.isna(window) else window,
            )
            actual_row = fm.iloc[[position]][list(expected.columns)]
            pd.testing.assert_frame_equal(
                actual_row.reset_index(drop=True),
                expected.reset_index(drop=True),
            )


class TestPerInstanceWindowErrors:
    def test_column_and_global_window_together_raise(self):
        es = make_shop_es()
        cutoff = windowed_cutoff(
            times=[(1, "2023-01-21")], windows=[pd.Timedelta("7d")],
        )
        with pytest.raises((ValueError, TypeError)):
            ft.calculate_feature_matrix(
                shop_features(es), entityset=es, cutoff_time=cutoff,
                training_window="15 days",
            )

    def test_column_and_global_window_together_raise_via_dfs(self):
        es = make_shop_es()
        cutoff = windowed_cutoff(
            times=[(1, "2023-01-21")], windows=[pd.Timedelta("7d")],
        )
        with pytest.raises((ValueError, TypeError)):
            ft.dfs(
                entityset=es, target_dataframe_name="customers",
                cutoff_time=cutoff, training_window="15 days",
                agg_primitives=["count"], trans_primitives=[],
            )

    def test_approximate_with_window_column_raises(self):
        es = make_shop_es()
        cutoff = windowed_cutoff(
            times=[(1, "2023-01-21")], windows=[pd.Timedelta("7d")],
        )
        with pytest.raises((ValueError, TypeError)):
            ft.calculate_feature_matrix(
                shop_features(es), entityset=es, cutoff_time=cutoff,
                approximate="1d",
            )

    def test_negative_window_raises(self):
        es = make_shop_es()
        cutoff = windowed_cutoff(
            times=[(1, "2023-01-21")], windows=[pd.Timedelta("-1d")],
        )
        with pytest.raises((ValueError, TypeError)):
            ft.calculate_feature_matrix(
                shop_features(es), entityset=es, cutoff_time=cutoff,
            )

    def test_string_window_value_raises(self):
        es = make_shop_es()
        cutoff = windowed_cutoff(
            times=[(1, "2023-01-21")], windows=["7d"],
        )
        with pytest.raises((ValueError, TypeError)):
            ft.calculate_feature_matrix(
                shop_features(es), entityset=es, cutoff_time=cutoff,
            )

    def test_numeric_value_on_datetime_entityset_raises(self):
        es = make_shop_es()
        cutoff = windowed_cutoff(
            times=[(1, "2023-01-21")], windows=[7],
        )
        with pytest.raises((ValueError, TypeError)):
            ft.calculate_feature_matrix(
                shop_features(es), entityset=es, cutoff_time=cutoff,
            )


class TestNumericTimeIndex:
    def test_global_numeric_window_now_supported(self):
        es = make_numeric_es()
        fm = ft.calculate_feature_matrix(
            numeric_features(es), entityset=es,
            cutoff_time=50, training_window=20,
        )
        # (30, 50] -> t=45 (device 1) and t=49 (device 2)
        assert fm["COUNT(logs)"].tolist() == [1, 1]
        assert fm["SUM(logs.v)"].tolist() == [3.0, 6.0]

    def test_global_numeric_window_float(self):
        es = make_numeric_es()
        fm = ft.calculate_feature_matrix(
            numeric_features(es), entityset=es,
            cutoff_time=50, training_window=35.5,
        )
        # (14.5, 50] -> t=30,45 and t=25,49
        assert fm["COUNT(logs)"].tolist() == [2, 2]
        assert fm["SUM(logs.v)"].tolist() == [5.0, 11.0]

    def test_per_instance_numeric_windows(self):
        es = make_numeric_es()
        cutoff = pd.DataFrame(
            {
                "instance_id": [1, 1, 2, 2],
                "time": [50, 60, 50, 60],
                "training_window": [20, None, 25, 5],
            },
        )
        fm = ft.calculate_feature_matrix(
            numeric_features(es), entityset=es, cutoff_time=cutoff,
        )
        # d1@50 w20 -> (30,50] -> 1 log; d1@60 full -> 3 logs
        # d2@50 w25 -> (25,50] -> 1 log; d2@60 w5 -> (55,60] -> 0 logs
        assert fm["COUNT(logs)"].tolist() == [1, 3, 1, 0]
        assert fm["SUM(logs.v)"].iloc[0] == 3.0
        assert fm["SUM(logs.v)"].iloc[1] == 6.0
        assert fm["SUM(logs.v)"].iloc[2] == 6.0
        assert fm["SUM(logs.v)"].iloc[3] == 0.0

    def test_numeric_window_lower_edge_exclusive(self):
        es = make_numeric_es()
        cutoff = pd.DataFrame(
            {
                "instance_id": [1, 1],
                "time": [50, 51],
                "training_window": [40, 42],
            },
        )
        fm = ft.calculate_feature_matrix(
            numeric_features(es), entityset=es, cutoff_time=cutoff,
        )
        # window 40 at t=50 -> (10, 50] excludes t=10 -> 2 logs
        # window 42 at t=51 -> (9, 51] includes t=10 -> 3 logs
        assert fm["COUNT(logs)"].tolist() == [2, 3]

    def test_numeric_zero_window(self):
        es = make_numeric_es()
        cutoff = pd.DataFrame(
            {"instance_id": [1], "time": [45], "training_window": [0]},
        )
        fm = ft.calculate_feature_matrix(
            numeric_features(es), entityset=es, cutoff_time=cutoff,
        )
        assert fm["COUNT(logs)"].iloc[0] == 0
        assert fm["SUM(logs.v)"].iloc[0] == 0.0

    def test_numeric_window_column_not_passed_through(self):
        es = make_numeric_es()
        cutoff = pd.DataFrame(
            {
                "instance_id": [1, 2],
                "time": [50, 50],
                "training_window": [20, None],
            },
        )
        fm = ft.calculate_feature_matrix(
            numeric_features(es), entityset=es, cutoff_time=cutoff,
        )
        assert "training_window" not in fm.columns
        assert fm["COUNT(logs)"].tolist() == [1, 3]

    def test_timedelta_value_on_numeric_entityset_raises(self):
        es = make_numeric_es()
        cutoff = pd.DataFrame(
            {
                "instance_id": [1],
                "time": [50],
                "training_window": [pd.Timedelta("2d")],
            },
        )
        with pytest.raises((ValueError, TypeError)):
            ft.calculate_feature_matrix(
                numeric_features(es), entityset=es, cutoff_time=cutoff,
            )

    def test_negative_numeric_window_raises(self):
        es = make_numeric_es()
        cutoff = pd.DataFrame(
            {"instance_id": [1], "time": [50], "training_window": [-5]},
        )
        with pytest.raises((ValueError, TypeError)):
            ft.calculate_feature_matrix(
                numeric_features(es), entityset=es, cutoff_time=cutoff,
            )

    def test_numeric_constant_column_matches_global(self):
        es = make_numeric_es()
        features = numeric_features(es)
        base_cutoff = pd.DataFrame({"instance_id": [1, 2], "time": [50, 50]})
        windowed = base_cutoff.copy()
        windowed["training_window"] = [20, 20]
        expected = ft.calculate_feature_matrix(
            features, entityset=es, cutoff_time=base_cutoff, training_window=20,
        )
        actual = ft.calculate_feature_matrix(
            features, entityset=es, cutoff_time=windowed,
        )
        pd.testing.assert_frame_equal(expected, actual)


# ---------------------------------------------------------------------------
# backward compatibility: these behaviors must be unchanged from the
# pristine repository (they pass both before and after the change)
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    def test_global_window_values_unchanged(self):
        es = make_shop_es()
        cutoff = pd.DataFrame(
            {
                "instance_id": [1, 2, 3],
                "time": pd.to_datetime(["2023-01-21"] * 3),
            },
        )
        fm = ft.calculate_feature_matrix(
            shop_features(es), entityset=es, cutoff_time=cutoff,
            training_window="15 days",
        )
        # (01-06, 01-21] per customer
        assert fm["COUNT(events)"].tolist() == [3, 2, 1]
        assert fm["SUM(events.amount)"].tolist() == [145.0, 45.0, 7.0]

    def test_no_window_values_unchanged(self):
        es = make_shop_es()
        cutoff = pd.DataFrame(
            {
                "instance_id": [1, 2, 3],
                "time": pd.to_datetime(["2023-01-21"] * 3),
            },
        )
        fm = ft.calculate_feature_matrix(
            shop_features(es), entityset=es, cutoff_time=cutoff,
        )
        assert fm["COUNT(events)"].tolist() == [5, 3, 1]
        assert fm["SUM(events.amount)"].tolist() == [175.0, 50.0, 7.0]
        assert fm["MEAN(events.amount)"].tolist() == [35.0, 50.0 / 3, 7.0]

    def test_pass_through_columns_unchanged(self):
        es = make_shop_es()
        cutoff = pd.DataFrame(
            {
                "instance_id": [1, 2],
                "time": pd.to_datetime(["2023-01-21"] * 2),
                "label": ["x", "y"],
                "score": [0.5, 0.75],
            },
        )
        fm = ft.calculate_feature_matrix(
            shop_features(es), entityset=es, cutoff_time=cutoff,
        )
        assert fm["label"].tolist() == ["x", "y"]
        assert fm["score"].tolist() == [0.5, 0.75]

    def test_duplicate_cutoff_rows_still_rejected(self):
        es = make_shop_es()
        cutoff = pd.DataFrame(
            {
                "instance_id": [1, 1],
                "time": pd.to_datetime(["2023-01-21"] * 2),
            },
        )
        with pytest.raises((AssertionError, ValueError)):
            ft.calculate_feature_matrix(
                shop_features(es), entityset=es, cutoff_time=cutoff,
            )

    def test_global_window_boundary_semantics_unchanged(self):
        es = make_shop_es()
        count = ft.Feature(
            es["events"].ww["event_id"],
            parent_dataframe_name="customers", primitive=Count,
        )
        # 20 day global window at 01-21: (01-01, 01-21] -> 4 events
        fm = ft.calculate_feature_matrix(
            [count], entityset=es, cutoff_time=pd.Timestamp("2023-01-21"),
            training_window="20 days",
        )
        assert fm.iloc[0, 0] == 4
        # include_cutoff_time=False: [01-01, 01-21) -> 4 events
        fm = ft.calculate_feature_matrix(
            [count], entityset=es, cutoff_time=pd.Timestamp("2023-01-21"),
            training_window="20 days", include_cutoff_time=False,
        )
        assert fm.iloc[0, 0] == 4

    def test_approximate_without_window_column_unchanged(self):
        es = make_shop_es()
        cutoff = pd.DataFrame(
            {
                "instance_id": [1, 2, 3],
                "time": pd.to_datetime(["2023-01-21"] * 3),
            },
        )
        fm = ft.calculate_feature_matrix(
            shop_features(es), entityset=es, cutoff_time=cutoff,
            approximate="1d",
        )
        assert fm["COUNT(events)"].tolist() == [5, 3, 1]

    def test_cutoff_time_in_index_without_window_column_unchanged(self):
        es = make_shop_es()
        cutoff = pd.DataFrame(
            {
                "instance_id": [1, 2],
                "time": pd.to_datetime(["2023-01-21", "2023-01-22"]),
            },
        )
        fm = ft.calculate_feature_matrix(
            shop_features(es), entityset=es, cutoff_time=cutoff,
            cutoff_time_in_index=True,
        )
        assert fm.index.names == ["customer_id", "time"]
        # c1 at 01-21 -> 5 events; c2 at 01-22 -> all 4 of its events
        assert fm["COUNT(events)"].tolist() == [5, 4]

    def test_instance_ids_with_single_cutoff_unchanged(self):
        es = make_shop_es()
        count = ft.Feature(
            es["events"].ww["event_id"],
            parent_dataframe_name="customers", primitive=Count,
        )
        fm = ft.calculate_feature_matrix(
            [count], entityset=es, cutoff_time=pd.Timestamp("2023-01-21"),
            instance_ids=[1, 3], training_window="7 days",
        )
        assert fm["COUNT(events)"].tolist() == [2, 1]

    def test_where_feature_global_window_unchanged(self):
        es = make_shop_es()
        where_feature = ft.Feature(
            es["events"].ww["event_id"],
            parent_dataframe_name="customers", primitive=Count,
            where=IdentityFeature(es["events"].ww["category"]) == "a",
        )
        fm = ft.calculate_feature_matrix(
            [where_feature], entityset=es,
            cutoff_time=pd.Timestamp("2023-01-21"), training_window="7 days",
        )
        # (01-14, 01-21] -> 01-20(b), 01-21(a) -> 1
        assert fm.iloc[0, 0] == 1

    def test_observations_window_still_rejected(self):
        es = make_shop_es()
        count = ft.Feature(
            es["events"].ww["event_id"],
            parent_dataframe_name="customers", primitive=Count,
        )
        with pytest.raises((AssertionError, ValueError)):
            ft.calculate_feature_matrix(
                [count], entityset=es,
                cutoff_time=pd.Timestamp("2023-01-21"),
                training_window=ft.Timedelta(2, "observations"),
            )

    def test_numeric_entityset_without_window_unchanged(self):
        es = make_numeric_es()
        fm = ft.calculate_feature_matrix(
            numeric_features(es), entityset=es, cutoff_time=50,
        )
        assert fm["COUNT(logs)"].tolist() == [3, 3]
        assert fm["SUM(logs.v)"].tolist() == [6.0, 15.0]
