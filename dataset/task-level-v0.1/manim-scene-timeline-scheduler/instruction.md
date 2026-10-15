# Add a declarative timeline to manim

I use manim in `/app` (the source of ManimCommunity/manim, with all dependencies installed, so `import manim` works directly) to make educational videos, and once the animations pile up, orchestrating them becomes unreadable.

Right now I can only call `self.play(...)` one by one inside `construct`: making an animation "start at exactly the 5-second mark", "wait 0.5 seconds after those two finish before starting", or "these segments must never collide" — all of that requires doing the math in my head and slicing things manually. On long videos, aligning the timeline alone eats half my effort, and changing one spot easily breaks everything after it. I want something like a video editor: lay the whole piece out on a timeline first, check it carefully, rescale the whole thing if needed, and then play it in one shot.

## How I expect to use it

```python
from manim import Timeline, Scene, Square, Circle, FadeIn, Write

class Demo(Scene):
    def construct(self):
        title = Square()
        formula = Circle()
        tl = Timeline()
        intro = tl.schedule(FadeIn(title), at=0.0)
        tl.schedule(Write(formula, run_time=2.0), after=intro, delay=0.5)
        tl.schedule(FadeIn(Circle()), at=6.0)

        # 检查布局，看着不对再改
        print(tl.duration)
        print(tl.report())

        # 一次播完。self.play 也能吃 Timeline，别搞两套 API
        self.play(tl)
```

I want `Timeline`, `ScheduledAnimation`, and `TimelineConflictError` to be importable directly via `from manim import ...` (they live in the `manim.scene.timeline` module, and are exactly the entire contents of that module's `__all__`); for `Scene`, besides `self.play(timeline)`, I also want to keep the old spelling `self.play_timeline(timeline, **kwargs)` (my old code uses it — don't make me rewrite everything).

## The behavior I want

- Schedule animations onto a timeline: specify an absolute start time `at`, or dependencies `after` on already-scheduled animations (including mixing multiple dependencies), plus an extra `delay` on top of a dependency or an absolute time; `at` and `after` may also be given together, in which case the effective start time is `max(at, the latest end among all dependencies) + delay`; if nothing is specified, it naturally appends after the existing content. It must reject invalid input on the spot with precise error types: a non-animation argument raises TypeError, while a negative `at`, a negative `delay`, an unknown dependency, or an entry borrowed from another timeline raises ValueError. The entry returned by `schedule` (a `ScheduledAnimation`) must expose `start`, `end`, `run_time`, `delay`, `index`, `animation`, and `dependencies` directly.
- A dependency can be either the entry returned by `schedule` or the original `Animation` object — as long as that object has already been scheduled on this timeline; an entry borrowed from another timeline does not count.
- Overlapping time points acting on the same mobject (or families that intersect, or one sub-animation of a composite animation colliding) — scheduling must raise an error on the spot, and the failed schedule must leave no trace (length, entry count, and index must not be consumed). Half-open intervals: touching end-to-start does not count as overlapping.
- After scheduling, it must be queryable: `active_at(t)` asks what is playing at a given time point; `entries_between(start, end)` asks what overlaps a given time window; `gaps()` asks which intervals are completely idle (end-to-start touching and overlaps must be merged first, and the stretch after the last entry does not count as idle); these queries must raise ValueError on the spot when given negative times or inverted windows. Produce a `report()` sorted by start time (ties broken by insertion order) with one row per animation: each row is a dict with exactly the fields `index` (insertion order), `animation` (animation class name), `start`, `end`, `run_time`, `targets` (sorted list of class names of the affected mobjects, an empty list for Wait) — no more, no fewer. The timeline itself must also expose `entries` (a tuple in insertion order), `len()`, `__iter__` (iterating the entries in insertion order), and `clear()`.
- A timeline can be rescaled as a whole via `rescale(factor)`: given a positive factor, return a new timeline where the animations are deep copies, start/delay/duration are scaled proportionally, and dependency relations are preserved within the new timeline; a non-positive factor must raise ValueError; the original timeline and animation objects must not be touched at all.
- Under the same "return a new timeline, never touch the original" principle, there must also be a few whole-timeline editing operations: `shift(offset)` shifts the start times of all entries by offset, dependencies are preserved within the new timeline, and a negative start time after shifting must raise a ValueError; `slice(start, end)` keeps only the entries that fall completely inside the window and resets the window start to 0, with negative start or end less than start raising ValueError; `merge(other, offset=None)` appends another timeline after this one, shifting the other side's entries by offset (the default offset is this timeline's own duration), and a non-Timeline `other` must raise TypeError; the `left + right` operator is equivalent to `merge` with the default offset.
- Track view: `tracks()` partitions the entries into several "tracks" such that entries within the same track never overlap in time (end-to-start touching is allowed on the same track), using as few tracks as possible; it returns a tuple (each track being a sequence of that track's entries), and an empty timeline returns an empty tuple.
- Wait is a pure wait — it affects no mobject and occupies no mobject's time window (that is, two Waits may fully overlap in time, and a Wait may fully overlap an animation that actually acts on a square).
- Playback: `build()` turns the timeline into a playable composite animation — concretely an `AnimationGroup` holding one `Succession(Wait(entry.start), entry.animation)` per entry, so every animation is uniformly wrapped in a "wait until its own scheduled moment, then play the animation itself" structure — even when that moment is 0, it is wrapped the same way (keeping the structure consistent for easier debugging); the total run time of the composite equals the timeline length. An empty timeline cannot be played — that means I forgot to schedule something, so `build()` on an empty timeline must raise ValueError on the spot.
- Make `Scene.play` accept a `Timeline` directly: `self.play(tl)`, `self.play_timeline(tl)`, and `self.play(tl.build())` — all three paths must behave identically; passing multiple Timelines in a single `play` must also be handled properly; `play_timeline` given something that is not a Timeline must raise TypeError. The old spelling `self.play(FadeIn(a), FadeIn(b))` must not regress in any way.
- While you're at it, add the basic operations this system relies on into `manim.animation`: `Animation.iter_leaves()` iterates all "leaf" animations out of any animation (including composite ones — composite animations are expanded recursively, and the same object is yielded only once); `Animation.affected_family()` asks an animation "which mobjects do you affect" (including family expansion; composite animations take the deduplicated union of their leaves; placeholders like Wait return an empty tuple); and add a companion `Animation.is_placeholder()` to mark placeholder animations (False for ordinary animations, True for `Wait`). Once done, the timeline's conflict detection / report / queries must go through these operations instead of rolling their own again.

The boundary I want goes beyond the list above — the semantics, naming style, and test conventions you find in the existing code are all implicit contracts, and must be honored too. When finished, add automated tests covering the behavior above (including conflicts, edge cases, rescaling, queries, and playback integration), following the organization and style of the repository's existing tests. The whole feature is pure logic: scheduling and querying must not render anything.
