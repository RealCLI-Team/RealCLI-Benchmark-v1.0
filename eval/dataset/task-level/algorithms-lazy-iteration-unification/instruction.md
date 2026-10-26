# Unify the lazy iteration behavior of this algorithms library

The repository is at /app; it's a Python algorithms collection library that we use directly in our data pipelines.

We've been hitting repeated problems in production lately, and tracing them back, the root cause is that this library's "enumeration-style" APIs behave completely inconsistently:

1. Within the same backtracking module, `permute_iter` and `palindromic_substrings_iter` are lazy generators, but sibling functions like `permute`, `subsets`, and `generate_parenthesis_v1` materialize all results into a list at once and return it. `array/flatten.py` is also half-and-half between `flatten` and `flatten_iter`. When we fed `permute` an input of a dozen-odd elements, memory blew up outright; we were forced to switch to the ones whose names carry `_iter`, and special-case checks are now scattered all over the code.
2. The tree traversals (pre/in/post-order, level-order, path enumeration, and the like) also compute the entire list in one go before returning. Our trees are deep, and callers often only need the first few values — right now, getting even the first value means waiting for the whole tree to be traversed.
3. `algorithms/heap` and `algorithms/queue` each have a `max_sliding_window`, and on edge cases like a window larger than the array length, empty arrays, or invalid window sizes, the two implementations contradict each other (one returns inexplicable results, the other fails in yet a different way), so downstream code can't handle them uniformly.

Please unify the whole library under one consistent lazy iteration convention:

- Every public API that "produces results item by item independently, where producing the first item does not require computing all results first" (enumeration, traversal, filtering, and the like) must be changed to return a single-consumption lazy iterator; the content and production order of each result item must match the current behavior item for item — after an explicit `list(...)`, callers must get exactly the same results as before.
- Conversely, APIs whose first result inherently depends on full computation (e.g. sorting, frequency counting, group aggregation, anything that must be globally sorted before it can produce output) keep returning materialized containers — don't force a change just for uniformity.
- Interfaces that are already lazily implemented stay as they are.
- Compatibility must not be broken: existing `*_iter`-named interfaces must remain usable; old call patterns that historically pass an accumulator list (e.g. recursive traversals taking a result list) must not directly raise errors; argument validation that used to throw immediately at call time (e.g. ValueError on invalid input) must still throw at call time, not deferred to consumption time.
- While you're at it, unify the edge-case behavior of the two sliding window implementations from point 3 above into one consistent, sensible semantics.
- After the changes, the library's own test suite must pass in full; the corresponding documentation (README, examples in module docstrings) must also be kept consistent with the new behavior.

Do not introduce any new third-party dependencies.
