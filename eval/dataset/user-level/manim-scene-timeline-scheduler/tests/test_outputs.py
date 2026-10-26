"""Verifier for the manim Timeline scheduling feature.

Every expected value in this file is a frozen constant derived from the
public behavioural contract of the feature; nothing is recomputed from
the state of the repository under test.  The suite is intentionally
strict on the introspection hooks that were introduced on top of the
existing :class:`~.Animation` / :class:`~.AnimationGroup` hierarchy,
and on the fact that :meth:`~.Scene.play` accepts :class:`Timeline`
directly (which requires modifying the existing :meth:`~.Scene.play`
rather than only bolting a new module on the side).
"""

from __future__ import annotations

import sys

import pytest

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from manim import (  # noqa: E402
    AnimationGroup,
    Circle,
    Dot,
    FadeIn,
    FadeOut,
    Indicate,
    LaggedStart,
    Line,
    Scene,
    Square,
    Succession,
    VGroup,
    Wait,
    Write,
    tempconfig,
)
from manim.animation.animation import Animation  # noqa: E402


@pytest.fixture()
def timeline_cls():
    from manim import Timeline

    return Timeline


def make_timeline():
    """Fixed reference schedule used by several checks.

    Layout:
        entry 0: FadeIn(Square)          [0.0, 1.0)
        entry 1: Write(Circle, rt=2.0)   [1.5, 3.5)   (after entry 0, delay 0.5)
        entry 2: FadeIn(Dot)             [1.5, 2.5)   (at = entry1.start)
    """
    from manim import Timeline

    timeline = Timeline()
    square, circle, dot = Square(), Circle(), Dot()
    entry0 = timeline.schedule(FadeIn(square), at=0.0)
    entry1 = timeline.schedule(Write(circle, run_time=2.0), after=entry0, delay=0.5)
    entry2 = timeline.schedule(FadeIn(dot), at=entry1.start)
    return timeline, (entry0, entry1, entry2), (square, circle, dot)


# ---------------------------------------------------------------- public API


def test_public_names_importable_from_top_level():
    import manim

    assert isinstance(manim.Timeline, type)
    assert isinstance(manim.ScheduledAnimation, type)
    assert issubclass(manim.TimelineConflictError, Exception)


def test_play_timeline_is_scene_method():
    assert callable(getattr(Scene, "play_timeline", None))


def test_timeline_module_all(timeline_cls):
    from manim.scene import timeline as timeline_module

    assert set(timeline_module.__all__) == {
        "Timeline",
        "ScheduledAnimation",
        "TimelineConflictError",
    }


# ------------------------------------------------------------ schedule rules


def test_schedule_explicit_at(timeline_cls):
    timeline = timeline_cls()
    entry = timeline.schedule(FadeIn(Square()), at=2.0)
    assert entry.start == 2.0
    assert entry.end == 3.0
    assert entry.run_time == 1.0
    assert entry.delay == 0.0
    assert entry.index == 0
    assert entry.dependencies == ()


def test_schedule_appends_after_current_content(timeline_cls):
    timeline = timeline_cls()
    first = timeline.schedule(FadeIn(Square(), run_time=2.0), at=1.0)
    second = timeline.schedule(FadeIn(Circle()))
    assert second.start == first.end == 3.0
    assert timeline.duration == 4.0


def test_schedule_after_entry_with_delay(timeline_cls):
    timeline = timeline_cls()
    first = timeline.schedule(FadeIn(Square(), run_time=2.0), at=1.0)
    second = timeline.schedule(FadeIn(Circle()), after=first, delay=0.5)
    assert second.start == 3.5
    assert second.dependencies == (first,)


def test_schedule_after_animation_object(timeline_cls):
    timeline = timeline_cls()
    animation = FadeIn(Square(), run_time=2.0)
    timeline.schedule(animation, at=1.0)
    entry = timeline.schedule(FadeIn(Circle()), after=animation)
    assert entry.start == 3.0


def test_schedule_after_multiple_dependencies_takes_latest_end(timeline_cls):
    timeline = timeline_cls()
    entry0 = timeline.schedule(FadeIn(Square(), run_time=1.0), at=0.0)
    entry1 = timeline.schedule(FadeIn(Circle(), run_time=4.0), at=0.5)
    entry2 = timeline.schedule(FadeIn(Dot(), run_time=2.0), at=2.0)
    dependent = timeline.schedule(FadeOut(Dot()), after=[entry0, entry1, entry2])
    assert dependent.start == 4.5


def test_schedule_at_and_after_take_max_plus_delay(timeline_cls):
    timeline = timeline_cls()
    first = timeline.schedule(FadeIn(Square(), run_time=2.0), at=0.0)
    assert timeline.schedule(FadeIn(Circle()), at=10.0, after=first).start == 10.0
    assert timeline.schedule(FadeIn(Dot()), at=1.0, after=first).start == 2.0
    assert (
        timeline.schedule(FadeIn(Square()), at=1.0, after=first, delay=0.5).start
        == 2.5
    )


def test_schedule_rejects_non_animation(timeline_cls):
    timeline = timeline_cls()
    with pytest.raises(TypeError):
        timeline.schedule(Square())


def test_schedule_rejects_negative_at(timeline_cls):
    timeline = timeline_cls()
    with pytest.raises(ValueError):
        timeline.schedule(FadeIn(Square()), at=-0.5)


def test_schedule_rejects_negative_delay(timeline_cls):
    timeline = timeline_cls()
    with pytest.raises(ValueError):
        timeline.schedule(FadeIn(Square()), at=1.0, delay=-0.1)


def test_schedule_rejects_unknown_dependency(timeline_cls):
    timeline = timeline_cls()
    with pytest.raises(ValueError):
        timeline.schedule(FadeIn(Circle()), after=FadeIn(Square()))


def test_schedule_rejects_foreign_entry(timeline_cls):
    timeline = timeline_cls()
    other = timeline_cls()
    foreign = other.schedule(FadeIn(Square()), at=0.0)
    with pytest.raises(ValueError):
        timeline.schedule(FadeIn(Circle()), after=foreign)


def test_failed_schedule_does_not_consume_an_index(timeline_cls):
    from manim import TimelineConflictError

    timeline = timeline_cls()
    square = Square()
    timeline.schedule(FadeIn(square), at=0.0)
    with pytest.raises(TimelineConflictError):
        timeline.schedule(FadeOut(square), at=0.5)
    entry = timeline.schedule(FadeIn(Circle()), at=0.5)
    assert entry.index == 1


# ------------------------------------------------------------- conflict rule


def test_conflict_on_same_mobject_overlap(timeline_cls):
    from manim import TimelineConflictError

    timeline = timeline_cls()
    square = Square()
    timeline.schedule(FadeIn(square), at=0.0)
    with pytest.raises(TimelineConflictError):
        timeline.schedule(FadeOut(square), at=0.5)
    # the failed schedule leaves the timeline unchanged
    assert len(timeline) == 1
    assert timeline.duration == 1.0


def test_no_conflict_when_windows_only_touch(timeline_cls):
    timeline = timeline_cls()
    square = Square()
    timeline.schedule(FadeIn(square), at=0.0)
    entry = timeline.schedule(FadeOut(square), at=1.0)
    assert entry.start == 1.0


def test_no_conflict_for_disjoint_mobjects(timeline_cls):
    timeline = timeline_cls()
    timeline.schedule(FadeIn(Square()), at=0.0)
    entry = timeline.schedule(FadeIn(Circle()), at=0.5)
    assert entry.start == 0.5


def test_conflict_via_family_intersection(timeline_cls):
    from manim import TimelineConflictError

    timeline = timeline_cls()
    square = Square()
    group = VGroup(square, Circle())
    timeline.schedule(FadeIn(group), at=0.0)
    with pytest.raises(TimelineConflictError):
        timeline.schedule(Indicate(square), at=0.5)


def test_conflict_via_composite_animation(timeline_cls):
    from manim import TimelineConflictError

    timeline = timeline_cls()
    circle = Circle()
    timeline.schedule(AnimationGroup(FadeIn(Square()), FadeIn(circle)), at=0.0)
    with pytest.raises(TimelineConflictError):
        timeline.schedule(Indicate(circle), at=0.5)


def test_conflict_via_shared_family_between_groups(timeline_cls):
    from manim import TimelineConflictError

    timeline = timeline_cls()
    shared = Square()
    left = VGroup(shared, Circle())
    right = VGroup(shared, Dot())
    timeline.schedule(FadeIn(left), at=0.0)
    with pytest.raises(TimelineConflictError):
        timeline.schedule(FadeIn(right), at=0.5)


def test_conflict_detected_via_lagged_start(timeline_cls):
    from manim import TimelineConflictError

    timeline = timeline_cls()
    square = Square()
    lagged = LaggedStart(FadeIn(square), FadeIn(Circle()), lag_ratio=0.5)
    timeline.schedule(lagged, at=0.0)
    with pytest.raises(TimelineConflictError):
        timeline.schedule(FadeOut(square), at=0.5)


# --------------------------- Wait must not conflict with anything -----------


def test_wait_does_not_conflict_with_wait(timeline_cls):
    """Wait animations do not touch any mobject, so any two Wait entries
    are free to overlap regardless of scheduling order."""

    timeline = timeline_cls()
    timeline.schedule(Wait(2.0), at=0.0)
    entry = timeline.schedule(Wait(3.0), at=0.5)
    assert entry.start == 0.5
    assert timeline.duration == 3.5


def test_wait_does_not_conflict_with_real_animation(timeline_cls):
    timeline = timeline_cls()
    timeline.schedule(FadeIn(Square()), at=0.0)
    entry = timeline.schedule(Wait(1.0), at=0.0)
    assert entry.start == 0.0


def test_wait_targets_are_empty(timeline_cls):
    """Wait's `targets` in the report is the empty list (Wait is a
    placeholder and does not act on any real mobject)."""

    timeline = timeline_cls()
    timeline.schedule(Wait(1.5), at=0.0)
    (row,) = timeline.report()
    assert row["animation"] == "Wait"
    assert row["targets"] == []


# ------------------------------------------------------- queries and reports


def test_duration_and_entries_container(timeline_cls):
    timeline, entries, _ = make_timeline()
    entry0, entry1, entry2 = entries
    assert timeline.entries == (entry0, entry1, entry2)
    assert len(timeline) == 3
    assert list(timeline) == [entry0, entry1, entry2]
    assert timeline.duration == 3.5
    timeline.clear()
    assert timeline.entries == ()
    assert len(timeline) == 0
    assert timeline.duration == 0.0


def test_active_at_half_open_windows(timeline_cls):
    timeline, entries, _ = make_timeline()
    entry0, entry1, entry2 = entries
    assert timeline.active_at(0.0) == (entry0,)
    assert timeline.active_at(1.0) == ()
    assert timeline.active_at(1.5) == (entry1, entry2)
    assert timeline.active_at(2.5) == (entry1,)
    assert timeline.active_at(3.5) == ()
    with pytest.raises(ValueError):
        timeline.active_at(-0.1)


def test_entries_between_overlap_semantics(timeline_cls):
    timeline, entries, _ = make_timeline()
    entry0, entry1, entry2 = entries
    assert timeline.entries_between(0.0, 10.0) == (entry0, entry1, entry2)
    assert timeline.entries_between(0.5, 1.0) == (entry0,)
    assert timeline.entries_between(1.0, 1.5) == ()
    assert timeline.entries_between(2.0, 2.0) == ()
    with pytest.raises(ValueError):
        timeline.entries_between(-1.0, 2.0)
    with pytest.raises(ValueError):
        timeline.entries_between(2.0, 1.0)


def test_gaps(timeline_cls):
    timeline, _, _ = make_timeline()
    assert timeline.gaps() == ((1.0, 1.5),)


def test_gaps_empty_and_dense(timeline_cls):
    assert timeline_cls().gaps() == ()
    timeline = timeline_cls()
    timeline.schedule(FadeIn(Square()), at=0.0)
    timeline.schedule(FadeIn(Circle()), at=1.0)
    assert timeline.gaps() == ()


def test_gaps_with_leading_and_middle(timeline_cls):
    timeline = timeline_cls()
    timeline.schedule(FadeIn(Square()), at=1.0)
    timeline.schedule(FadeIn(Circle()), at=4.0)
    timeline.schedule(FadeIn(Dot()), at=7.0)
    assert timeline.gaps() == ((0.0, 1.0), (2.0, 4.0), (5.0, 7.0))


def test_report_exact_content(timeline_cls):
    timeline, _, _ = make_timeline()
    assert timeline.report() == [
        {
            "index": 0,
            "animation": "FadeIn",
            "start": 0.0,
            "end": 1.0,
            "run_time": 1.0,
            "targets": ["Square"],
        },
        {
            "index": 1,
            "animation": "Write",
            "start": 1.5,
            "end": 3.5,
            "run_time": 2.0,
            "targets": ["Circle"],
        },
        {
            "index": 2,
            "animation": "FadeIn",
            "start": 1.5,
            "end": 2.5,
            "run_time": 1.0,
            "targets": ["Dot"],
        },
    ]


def test_report_sorted_by_start_then_index(timeline_cls):
    timeline = timeline_cls()
    timeline.schedule(FadeIn(Square()), at=3.0)
    timeline.schedule(FadeIn(Circle()), at=0.0)
    timeline.schedule(FadeIn(Dot()), at=0.0)
    report = timeline.report()
    assert [row["index"] for row in report] == [1, 2, 0]


def test_report_keys_are_exactly_documented(timeline_cls):
    timeline = timeline_cls()
    timeline.schedule(FadeIn(Square()), at=0.0)
    (row,) = timeline.report()
    assert set(row) == {"index", "animation", "start", "end", "run_time", "targets"}


def test_report_targets_for_composite(timeline_cls):
    timeline = timeline_cls()
    timeline.schedule(
        AnimationGroup(FadeIn(Square()), FadeIn(Circle())), at=0.0
    )
    (row,) = timeline.report()
    assert row["targets"] == ["Circle", "Square"]


# -------------------------------------------------------------------- build


def test_build_empty_timeline_raises(timeline_cls):
    with pytest.raises(ValueError):
        timeline_cls().build()


def test_build_structure(timeline_cls):
    timeline, entries, _ = make_timeline()
    built = timeline.build()
    assert isinstance(built, AnimationGroup)
    assert built.run_time == pytest.approx(3.5)
    assert len(built.animations) == 3
    for child, entry in zip(built.animations, entries):
        assert isinstance(child, Succession)
        wait, animation = child.animations
        assert isinstance(wait, Wait)
        assert wait.run_time == entry.start
        assert animation is entry.animation


def test_build_zero_start_still_wrapped_in_wait(timeline_cls):
    timeline = timeline_cls()
    timeline.schedule(FadeIn(Square()), at=0.0)
    built = timeline.build()
    wait = built.animations[0].animations[0]
    assert isinstance(wait, Wait)
    assert wait.run_time == 0.0


# ------------------------------------------------------------------ rescale


def test_rescale_scales_everything_and_copies(timeline_cls):
    timeline, entries, _ = make_timeline()
    scaled = timeline.rescale(2.0)
    assert [(e.start, e.end) for e in scaled.entries] == [
        (0.0, 2.0),
        (3.0, 7.0),
        (3.0, 5.0),
    ]
    assert scaled.duration == 7.0
    for original, copy in zip(timeline.entries, scaled.entries):
        assert copy.animation is not original.animation
        assert type(copy.animation) is type(original.animation)
    assert [(e.start, e.end) for e in timeline.entries] == [
        (0.0, 1.0),
        (1.5, 3.5),
        (1.5, 2.5),
    ]
    assert scaled.entries[1].dependencies == (scaled.entries[0],)


def test_rescale_rejects_non_positive_factor(timeline_cls):
    timeline, _, _ = make_timeline()
    with pytest.raises(ValueError):
        timeline.rescale(0.0)
    with pytest.raises(ValueError):
        timeline.rescale(-1.0)


# ------------------------------------------------------- shift / slice / merge / add


def test_shift_moves_starts_and_keeps_dependencies(timeline_cls):
    timeline = timeline_cls()
    first = timeline.schedule(FadeIn(Square()), at=0.0)
    timeline.schedule(FadeIn(Circle()), after=first, delay=0.5)
    shifted = timeline.shift(2.0)
    assert [e.start for e in shifted.entries] == [2.0, 3.5]
    assert shifted.entries[1].dependencies == (shifted.entries[0],)


def test_shift_rejects_negative_result(timeline_cls):
    timeline = timeline_cls()
    timeline.schedule(FadeIn(Square()), at=1.0)
    with pytest.raises(ValueError):
        timeline.shift(-2.0)


def test_slice_keeps_only_contained_entries(timeline_cls):
    timeline = timeline_cls()
    timeline.schedule(FadeIn(Square()), at=0.0)
    timeline.schedule(FadeIn(Circle()), at=2.0)
    timeline.schedule(FadeIn(Dot()), at=4.0)
    sliced = timeline.slice(1.5, 3.5)
    assert len(sliced) == 1
    assert sliced.entries[0].start == 0.5


def test_slice_validation(timeline_cls):
    timeline = timeline_cls()
    with pytest.raises(ValueError):
        timeline.slice(-0.1, 1.0)
    with pytest.raises(ValueError):
        timeline.slice(2.0, 1.0)


def test_merge_default_offset_is_duration(timeline_cls):
    left = timeline_cls()
    left.schedule(FadeIn(Square(), run_time=2.0), at=0.0)
    right = timeline_cls()
    right.schedule(FadeIn(Circle()), at=0.0)
    merged = left.merge(right)
    assert [e.start for e in merged.entries] == [0.0, 2.0]
    assert merged.duration == 3.0


def test_merge_explicit_offset(timeline_cls):
    left = timeline_cls()
    left.schedule(FadeIn(Square()), at=0.0)
    right = timeline_cls()
    right.schedule(FadeIn(Circle()), at=0.0)
    merged = left.merge(right, offset=5.0)
    assert [e.start for e in merged.entries] == [0.0, 5.0]


def test_merge_requires_timeline(timeline_cls):
    with pytest.raises(TypeError):
        timeline_cls().merge("not a timeline")


def test_add_operator_concatenates(timeline_cls):
    left = timeline_cls()
    left.schedule(FadeIn(Square(), run_time=2.0), at=0.0)
    right = timeline_cls()
    right.schedule(FadeIn(Circle()), at=0.0)
    added = left + right
    assert [e.start for e in added.entries] == [0.0, 2.0]


# -------------------------------------------------------------------- tracks


def test_tracks_partitions_non_overlapping(timeline_cls):
    timeline = timeline_cls()
    timeline.schedule(FadeIn(Square()), at=0.0)
    timeline.schedule(FadeIn(Circle()), at=2.0)
    tracks = timeline.tracks()
    assert len(tracks) == 1
    assert len(tracks[0]) == 2


def test_tracks_splits_when_overlapping(timeline_cls):
    timeline = timeline_cls()
    timeline.schedule(FadeIn(Square()), at=0.0)
    timeline.schedule(FadeIn(Circle()), at=0.5)
    tracks = timeline.tracks()
    assert len(tracks) == 2


def test_tracks_touching_stays_on_one_track(timeline_cls):
    timeline = timeline_cls()
    timeline.schedule(FadeIn(Square()), at=0.0)
    timeline.schedule(FadeIn(Circle()), at=1.0)
    tracks = timeline.tracks()
    assert len(tracks) == 1


def test_tracks_empty_timeline(timeline_cls):
    assert timeline_cls().tracks() == ()


# ------------------------------------------------------- scene integration


def test_play_timeline_rejects_non_timeline(timeline_cls):
    with tempconfig({"dry_run": True, "quality": "low_quality"}):
        scene = Scene()
        with pytest.raises(TypeError):
            scene.play_timeline("not a timeline")


def test_play_timeline_plays_built_composition(timeline_cls):
    """`scene.play_timeline(timeline)` must delegate to `scene.play`;
    when the user overrides `play`, `play_timeline` uses that override."""

    recorded = []

    class RecordingScene(Scene):
        def play(self, *args, **kwargs):
            recorded.append((args, kwargs))

    timeline, _, _ = make_timeline()
    with tempconfig({"dry_run": True, "quality": "low_quality"}):
        scene = RecordingScene()
        scene.play_timeline(timeline)
    assert len(recorded) == 1
    (played,) = recorded[0][0]
    assert played is timeline


def test_scene_play_accepts_timeline_directly(timeline_cls):
    """`scene.play(timeline)` and `scene.play_timeline(timeline)` must
    be strictly equivalent: both convert the timeline to an
    :class:`~.AnimationGroup` (via :meth:`Timeline.build`) before it
    reaches the renderer.  We validate this by hooking `renderer.play`
    on a dry-run scene."""

    class HookingScene(Scene):
        def __init__(self):
            super().__init__()
            self._captured = []

        def _install_hook(self):
            captured = self._captured

            def _capture(_scene, *args, **kwargs):
                captured.append(args)
                return None

            self.renderer.play = _capture

    timeline, _, _ = make_timeline()

    with tempconfig({"dry_run": True, "quality": "low_quality"}):
        scene = HookingScene()
        scene._install_hook()
        scene.play(timeline)

    assert len(scene._captured) == 1
    (played,) = scene._captured[0]
    assert isinstance(played, AnimationGroup)
    assert played.run_time == pytest.approx(timeline.duration)


def test_play_timeline_end_to_end_render(timeline_cls):
    class TimelineScene(Scene):
        def construct(self):
            self.square = Square()
            self.circle = Circle()
            timeline = timeline_cls()
            timeline.schedule(FadeIn(self.square), at=0.0)
            timeline.schedule(FadeIn(self.circle), at=0.5)
            timeline.schedule(FadeOut(self.square), at=2.0)
            self.play_timeline(timeline)

    with tempconfig({"dry_run": True, "quality": "low_quality"}):
        scene = TimelineScene()
        scene.render()
        assert scene.time == pytest.approx(3.0, abs=0.01)
        assert scene.square not in scene.mobjects
        assert scene.circle in scene.mobjects


def test_scene_play_of_timeline_end_to_end_render(timeline_cls):
    """`self.play(timeline)` from inside construct must render exactly
    like `self.play_timeline(timeline)`."""

    class DirectScene(Scene):
        def construct(self):
            self.square = Square()
            self.circle = Circle()
            timeline = timeline_cls()
            timeline.schedule(FadeIn(self.square), at=0.0)
            timeline.schedule(FadeIn(self.circle), at=0.5)
            timeline.schedule(FadeOut(self.square), at=2.0)
            self.play(timeline)

    with tempconfig({"dry_run": True, "quality": "low_quality"}):
        scene = DirectScene()
        scene.render()
        assert scene.time == pytest.approx(3.0, abs=0.01)
        assert scene.square not in scene.mobjects
        assert scene.circle in scene.mobjects


# -------------------- Animation introspection hooks (existing-file mods) ----


def test_animation_iter_leaves_default_returns_self():
    """Plain :class:`Animation` yields itself as its only leaf."""

    anim = FadeIn(Square())
    leaves = list(anim.iter_leaves())
    assert leaves == [anim]


def test_animation_group_iter_leaves_recurses():
    """AnimationGroup and its subclasses recurse to reveal the leaf
    animations, in traversal order."""

    a = FadeIn(Square())
    b = FadeIn(Circle())
    c = FadeIn(Dot())
    group = Succession(AnimationGroup(a, b), c)
    assert list(group.iter_leaves()) == [a, b, c]


def test_animation_group_iter_leaves_deduplicates_by_identity():
    """If the same animation object appears twice in a nested tree,
    :meth:`AnimationGroup.iter_leaves` yields it only once."""

    shared = FadeIn(Square())
    group = AnimationGroup(shared, AnimationGroup(shared, FadeIn(Circle())))
    leaves = list(group.iter_leaves())
    assert leaves.count(shared) == 1
    # And the sibling is still yielded exactly once
    circle_leaves = [leaf for leaf in leaves if isinstance(leaf.mobject, Circle)]
    assert len(circle_leaves) == 1


def test_animation_is_placeholder_default_false():
    assert FadeIn(Square()).is_placeholder() is False


def test_wait_is_placeholder_true():
    assert Wait(1.0).is_placeholder() is True


def test_animation_affected_family_returns_family_tuple():
    group = VGroup(Square(), Circle())
    family = FadeIn(group).affected_family()
    assert isinstance(family, tuple)
    # First member of the family is the group itself
    assert family[0] is group
    # All members are Mobjects
    from manim.mobject.mobject import Mobject as _M
    for m in family:
        assert isinstance(m, _M)


def test_animation_group_affected_family_unions_leaves():
    """AnimationGroup.affected_family returns the union of leaf families
    with per-identity deduplication."""

    square, circle = Square(), Circle()
    group = AnimationGroup(FadeIn(square), FadeIn(circle))
    family_ids = {id(m) for m in group.affected_family()}
    assert id(square) in family_ids
    assert id(circle) in family_ids


def test_wait_affected_family_is_empty():
    """Wait declares itself a placeholder and returns an empty affected
    family, so any Wait can freely overlap with any other animation."""

    assert Wait(1.0).affected_family() == ()


def test_composite_including_wait_only_reports_real_leaves():
    """A composite whose leaves are `Wait, FadeIn(square)` reports
    just the square in its affected family."""

    square = Square()
    group = AnimationGroup(Wait(1.0), FadeIn(square))
    family = group.affected_family()
    assert any(m is square for m in family)


# ------------------------------------------------------- regression contracts


def test_regression_scene_play_still_accepts_plain_animation():
    """Adding Timeline expansion to Scene.play must not break the
    ordinary `scene.play(FadeIn(square))` path."""

    class RecordingScene(Scene):
        def __init__(self):
            super().__init__()
            self.saw = []

        def _hook(self):
            saw = self.saw
            def _capture(_scene, *args, **kwargs):
                saw.append(args)
            self.renderer.play = _capture

    with tempconfig({"dry_run": True, "quality": "low_quality"}):
        scene = RecordingScene()
        scene._hook()
        anim = FadeIn(Square())
        scene.play(anim)
    (args,) = scene.saw
    assert len(args) == 1
    assert args[0] is anim


def test_regression_scene_play_still_accepts_multiple_animations():
    class RecordingScene(Scene):
        def __init__(self):
            super().__init__()
            self.saw = []

        def _hook(self):
            saw = self.saw
            def _capture(_scene, *args, **kwargs):
                saw.append(args)
            self.renderer.play = _capture

    with tempconfig({"dry_run": True, "quality": "low_quality"}):
        scene = RecordingScene()
        scene._hook()
        a, b = FadeIn(Square()), FadeIn(Circle())
        scene.play(a, b)
    (args,) = scene.saw
    assert args == (a, b)


def test_regression_animation_group_still_has_animations_attribute():
    a = FadeIn(Square())
    b = FadeIn(Circle())
    group = AnimationGroup(a, b)
    assert group.animations[0] is a
    assert group.animations[1] is b


def test_regression_animation_group_run_time_still_computed_from_children():
    """Adding introspection hooks to AnimationGroup must not change how
    it computes its run_time from its children."""

    group = AnimationGroup(FadeIn(Square(), run_time=2.0), FadeIn(Circle()))
    assert group.run_time == 2.0


def test_regression_succession_still_plays_children_in_order():
    a = FadeIn(Square(), run_time=1.0)
    b = FadeIn(Circle(), run_time=2.0)
    succession = Succession(a, b)
    assert succession.animations == [a, b]
    assert succession.run_time == pytest.approx(3.0)


def test_regression_wait_still_supports_run_time_constructor():
    wait = Wait(2.5)
    assert wait.run_time == 2.5


def test_regression_wait_still_interpolates_without_side_effects():
    """Wait's begin/interpolate/finish contract must remain a no-op."""

    wait = Wait(1.0)
    wait.begin()
    wait.interpolate(0.5)
    wait.finish()  # must not raise
