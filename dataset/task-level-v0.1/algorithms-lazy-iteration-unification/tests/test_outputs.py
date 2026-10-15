"""Verifier for the lazy-iteration unification task on /app (keon/algorithms).

Checks, against the agent's workspace at /app:

1. Enumeration-style public APIs return single-pass lazy iterators whose
   items match frozen golden sequences (content and historical order).
2. First-item latency is independent of the total result count (true
   laziness, not ``iter(eager_list)``).
3. Backward-compatibility contracts: legacy ``*_iter`` aliases, legacy
   accumulator forms, eager argument validation, unified sliding-window
   edge semantics across the two ``max_sliding_window`` implementations.
4. APIs whose first result needs the whole computation still return
   materialized containers (no over-conversion).
5. The repository's own test suite at /app/tests still passes.

All expected values are frozen golden data (tests/golden.json), captured
once from the pre-change release; nothing is recomputed from /app state.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, "/app")

GOLDEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden.json")
with open(GOLDEN_PATH, encoding="utf-8") as fh:
    GOLDEN = json.load(fh)

BOARD = [["o", "a", "a", "n"], ["e", "t", "a", "e"], ["i", "h", "k", "r"], ["i", "f", "l", "v"]]
WORDS = ["Amazon", "Microsoft", "Google"]
SYMBOLS = ["i", "Am", "cro", "le", "abc"]
NUMS = [1, 3, -1, -3, 5, 3, 6, 7]
ARR2 = [8, 5, 10, 7, 9, 4, 15, 12, 90, 13]

# (golden_key, module, function, args, order_matters)
CASES = [
    ("subsets_123", "algorithms.backtracking.subsets", "subsets", ([1, 2, 3],), True),
    ("subsets_empty", "algorithms.backtracking.subsets", "subsets", ([],), True),
    ("subsets_single", "algorithms.backtracking.subsets", "subsets", ([5],), True),
    ("subsets_v2_123", "algorithms.backtracking.subsets", "subsets_v2", ([1, 2, 3],), True),
    ("subsets_v2_246", "algorithms.backtracking.subsets", "subsets_v2", ([2, 4, 6],), True),
    ("subsets_unique_122", "algorithms.backtracking.subsets_unique", "subsets_unique", ([1, 2, 2],), False),
    ("combination_sum_7", "algorithms.backtracking.combination_sum", "combination_sum", ([2, 3, 6, 7], 7), True),
    ("combination_sum_8", "algorithms.backtracking.combination_sum", "combination_sum", ([2, 3, 5], 8), True),
    ("combination_sum_none", "algorithms.backtracking.combination_sum", "combination_sum", ([2], 1), True),
    ("gen_paren_v1_3", "algorithms.backtracking.generate_parenthesis", "generate_parenthesis_v1", (3,), True),
    ("gen_paren_v2_3", "algorithms.backtracking.generate_parenthesis", "generate_parenthesis_v2", (3,), True),
    ("gen_paren_v1_1", "algorithms.backtracking.generate_parenthesis", "generate_parenthesis_v1", (1,), True),
    ("gen_abbrev_word", "algorithms.backtracking.generate_abbreviations", "generate_abbreviations", ("word",), True),
    ("add_operators_123_6", "algorithms.backtracking.add_operators", "add_operators", ("123", 6), True),
    ("add_operators_232_8", "algorithms.backtracking.add_operators", "add_operators", ("232", 8), True),
    ("array_sum_comb", "algorithms.backtracking.array_sum_combinations", "array_sum_combinations", ([1, 2], [2, 3], [3, 4], 6), True),
    ("unique_array_sum_comb", "algorithms.backtracking.array_sum_combinations", "unique_array_sum_combinations", ([1, 2], [2, 3], [3, 4], 6), False),
    ("factor_get_factors_32", "algorithms.backtracking.factor_combinations", "get_factors", (32,), True),
    ("factor_recursive_32", "algorithms.backtracking.factor_combinations", "recursive_get_factors", (32,), True),
    ("find_words", "algorithms.backtracking.find_words", "find_words", (BOARD, ["oath", "pea", "eat", "rain"]), False),
    ("letter_comb_23", "algorithms.backtracking.letter_combination", "letter_combinations", ("23",), True),
    ("letter_comb_empty", "algorithms.backtracking.letter_combination", "letter_combinations", ("",), True),
    ("permute_123", "algorithms.backtracking.permute", "permute", ([1, 2, 3],), True),
    ("permute_str_ab", "algorithms.backtracking.permute", "permute", ("ab",), True),
    ("permute_str_abc", "algorithms.backtracking.permute", "permute", ("abc",), True),
    ("permute_single", "algorithms.backtracking.permute", "permute", ([7],), True),
    ("permute_recursive_123", "algorithms.backtracking.permute", "permute_recursive", ([1, 2, 3],), True),
    ("permute_iter_12", "algorithms.backtracking.permute", "permute_iter", ([1, 2],), True),
    ("permute_unique_112", "algorithms.backtracking.permute_unique", "permute_unique", ([1, 1, 2],), True),
    ("pal_sub_aab", "algorithms.backtracking.palindrome_partitioning", "palindromic_substrings", ("aab",), True),
    ("pal_sub_iter_aab", "algorithms.backtracking.palindrome_partitioning", "palindromic_substrings_iter", ("aab",), True),
    ("missing_ranges", "algorithms.array.missing_ranges", "missing_ranges", ([0, 1, 3, 50, 75], 0, 99), True),
    ("missing_ranges_empty", "algorithms.array.missing_ranges", "missing_ranges", ([], 1, 5), True),
    ("missing_ranges_repo", "algorithms.array.missing_ranges", "missing_ranges", ([3, 5, 10, 11, 12, 15, 19], 0, 20), True),
    ("summarize_ranges", "algorithms.array.summarize_ranges", "summarize_ranges", ([0, 1, 2, 4, 5, 7],), True),
    ("summarize_ranges_empty", "algorithms.array.summarize_ranges", "summarize_ranges", ([],), True),
    ("summarize_ranges_single", "algorithms.array.summarize_ranges", "summarize_ranges", ([5],), True),
    ("summarize_ranges_neg", "algorithms.array.summarize_ranges", "summarize_ranges", ([-5, -4, -3, 1, 2, 4, 5, 6],), True),
    ("delete_nth", "algorithms.array.delete_nth", "delete_nth", ([1, 2, 3, 1, 2, 1, 2, 3], 2), True),
    ("delete_nth_naive", "algorithms.array.delete_nth", "delete_nth_naive", ([1, 2, 3, 1, 2, 1, 2, 3], 2), True),
    ("three_sum", "algorithms.array.three_sum", "three_sum", ([-1, 0, 1, 2, -1, -4],), False),
    ("three_sum2", "algorithms.array.three_sum", "three_sum", ([-1, 3, 1, 2, -1, -4, -2],), False),
    ("limit_default", "algorithms.array.limit", "limit", ([1, 2, 3, 4, 5],), True),
    ("limit_bounded", "algorithms.array.limit", "limit", ([1, 2, 3, 4, 5], 2, 4), True),
    ("fizzbuzz_15", "algorithms.string.fizzbuzz", "fizzbuzz", (15,), True),
    ("fizzbuzz_helper_15", "algorithms.string.fizzbuzz", "fizzbuzz_with_helper_func", (15,), True),
    ("word_squares", "algorithms.string.word_squares", "word_squares", (["area", "lead", "wall", "lady", "ball"],), True),
    ("match_symbol", "algorithms.string.breaking_bad", "match_symbol", (WORDS, SYMBOLS), True),
    ("match_symbol_1", "algorithms.string.breaking_bad", "match_symbol_1", (WORDS, SYMBOLS), True),
    ("spiral_3x3", "algorithms.matrix.spiral_traversal", "spiral_traversal", ([[1, 2, 3], [4, 5, 6], [7, 8, 9]],), True),
    ("spiral_row", "algorithms.matrix.spiral_traversal", "spiral_traversal", ([[1, 2, 3, 4]],), True),
    ("spiral_col", "algorithms.matrix.spiral_traversal", "spiral_traversal", ([[1], [2], [3]],), True),
    ("spiral_empty", "algorithms.matrix.spiral_traversal", "spiral_traversal", ([],), True),
    ("heap_msw_k3", "algorithms.heap.sliding_window_max", "max_sliding_window", (NUMS, 3), True),
    ("heap_msw_k1", "algorithms.heap.sliding_window_max", "max_sliding_window", ([2, 1, 5], 1), True),
    ("heap_msw_arr2_k2", "algorithms.heap.sliding_window_max", "max_sliding_window", (ARR2, 2), True),
    ("queue_msw_k3", "algorithms.queue.max_sliding_window", "max_sliding_window", (NUMS, 3), True),
    ("queue_msw_k1", "algorithms.queue.max_sliding_window", "max_sliding_window", ([2, 1, 5], 1), True),
    ("queue_msw_k5", "algorithms.queue.max_sliding_window", "max_sliding_window", (NUMS, 5), True),
    ("queue_msw_k7", "algorithms.queue.max_sliding_window", "max_sliding_window", (NUMS, 7), True),
    ("queue_msw_arr2_k4", "algorithms.queue.max_sliding_window", "max_sliding_window", (ARR2, 4), True),
    ("graph_get_factors_60", "algorithms.graph.all_factors", "get_factors", (60,), True),
    ("graph_get_factors_it1_60", "algorithms.graph.all_factors", "get_factors_iterative1", (60,), True),
    ("graph_get_factors_it2_60", "algorithms.graph.all_factors", "get_factors_iterative2", (60,), True),
    ("strobo_1", "algorithms.math.generate_strobogrammtic", "gen_strobogrammatic", (1,), True),
    ("strobo_2", "algorithms.math.generate_strobogrammtic", "gen_strobogrammatic", (2,), True),
    ("strobo_3", "algorithms.math.generate_strobogrammtic", "gen_strobogrammatic", (3,), True),
    ("find_keyboard_row", "algorithms.set.find_keyboard_row", "find_keyboard_row", (["Hello", "Alaska", "Dad", "Peace"],), True),
]


def _import(module_name, func_name):
    import importlib

    return getattr(importlib.import_module(module_name), func_name)


def _canon_items(items):
    return [tuple(item) if isinstance(item, list) else item for item in items]


def _canon_sorted(items):
    return sorted(map(repr, _canon_items(items)))


def build_sample_tree():
    r"""Shared sample tree:

            1
           / \
          2   3
         / \   \
        4   5   6
           /
          7
    """
    from algorithms.common.tree_node import TreeNode

    def node(val, left=None, right=None):
        item = TreeNode(val)
        item.left = left
        item.right = right
        return item

    return node(1, node(2, node(4), node(5, node(7))), node(3, None, node(6)))


def build_view_tree():
    r"""Asymmetric tree for the view functions:

            1
           / \
          2   3
           \ / \
           4 5  6
    """
    from algorithms.common.tree_node import TreeNode

    def node(val, left=None, right=None):
        item = TreeNode(val)
        item.left = left
        item.right = right
        return item

    return node(1, node(2, None, node(4)), node(3, node(5), node(6)))


@pytest.mark.parametrize(
    "key,module,func,args,ordered",
    CASES,
    ids=[c[0] for c in CASES],
)
def test_lazy_content_and_order(key, module, func, args, ordered):
    api = _import(module, func)
    result = api(*args)
    assert not isinstance(result, (list, tuple, set, frozenset, dict, str)), (
        f"{module}.{func} must return a lazy iterator, got {type(result).__name__}"
    )
    assert iter(result) is result, f"{module}.{func} must return a single-pass iterator"
    actual = list(result)
    expected = GOLDEN[key]
    if ordered:
        assert _canon_items(actual) == _canon_items(expected)
    else:
        assert _canon_sorted(actual) == _canon_sorted(expected)
    assert list(result) == [], "iterator must be exhausted after one full pass"


TREE_CASES = [
    ("inorder", "algorithms.tree.traversal_inorder", "inorder"),
    ("inorder_rec", "algorithms.tree.traversal_inorder", "inorder_rec"),
    ("preorder", "algorithms.tree.traversal_preorder", "preorder"),
    ("preorder_rec", "algorithms.tree.traversal_preorder", "preorder_rec"),
    ("postorder", "algorithms.tree.traversal_postorder", "postorder"),
    ("postorder_rec", "algorithms.tree.traversal_postorder", "postorder_rec"),
    ("level_order", "algorithms.tree.traversal_level_order", "level_order"),
    ("zigzag_level", "algorithms.tree.traversal_zigzag", "zigzag_level"),
    ("binary_tree_paths", "algorithms.tree.binary_tree_paths", "binary_tree_paths"),
]


@pytest.mark.parametrize("key,module,func", TREE_CASES, ids=[c[0] for c in TREE_CASES])
def test_tree_traversals_lazy(key, module, func):
    api = _import(module, func)
    result = api(build_sample_tree())
    assert not isinstance(result, (list, tuple, set, frozenset, dict, str))
    assert iter(result) is result
    assert _canon_items(list(result)) == _canon_items(GOLDEN[key])
    assert list(result) == []


@pytest.mark.parametrize(
    "module,func",
    [
        ("algorithms.tree.traversal_inorder", "inorder"),
        ("algorithms.tree.traversal_preorder", "preorder"),
        ("algorithms.tree.traversal_postorder", "postorder"),
        ("algorithms.tree.traversal_level_order", "level_order"),
        ("algorithms.tree.binary_tree_paths", "binary_tree_paths"),
    ],
)
def test_tree_empty_input(module, func):
    api = _import(module, func)
    result = api(None)
    assert iter(result) is result
    assert list(result) == []


@pytest.mark.parametrize(
    "func", ["path_sum", "path_sum2", "path_sum3"]
)
def test_path_sum_variants(func):
    api = _import("algorithms.tree.path_sum2", func)
    result = api(build_sample_tree(), 15)
    assert iter(result) is result
    assert _canon_items(list(result)) == _canon_items(GOLDEN["path_sum_15"])
    assert list(api(build_sample_tree(), 100)) == []
    assert list(api(None, 0)) == []


@pytest.mark.parametrize(
    "key,func",
    [("left_view", "left_view"), ("right_view", "right_view")],
)
def test_tree_views_lazy(key, func):
    api = _import("algorithms.tree.binary_tree_views", func)
    result = api(build_view_tree())
    assert iter(result) is result
    assert list(result) == GOLDEN[key]
    assert list(api(None)) == []


def test_legacy_accumulator_forms_still_work():
    from algorithms.tree.traversal_inorder import inorder_rec
    from algorithms.tree.traversal_postorder import postorder_rec
    from algorithms.tree.traversal_preorder import preorder_rec

    acc = []
    returned = inorder_rec(build_sample_tree(), acc)
    assert isinstance(returned, list)
    assert acc == GOLDEN["inorder"]
    acc2 = []
    preorder_rec(build_sample_tree(), acc2)
    assert acc2 == GOLDEN["preorder"]
    acc3 = []
    postorder_rec(build_sample_tree(), acc3)
    assert acc3 == GOLDEN["postorder"]

    from algorithms.array.flatten import flatten

    acc4 = [0]
    returned4 = flatten([1, [2, [3]]], acc4)
    assert isinstance(returned4, list)
    assert acc4 == [0, 1, 2, 3]


def test_flatten_is_lazy():
    from algorithms.array.flatten import flatten, flatten_iter

    result = flatten([1, [2, [3, 4], 5]])
    assert not isinstance(result, (list, tuple, set, frozenset, dict, str))
    assert iter(result) is result
    assert list(result) == GOLDEN["flatten"]
    assert list(flatten_iter([2, [3, [4]]])) == [2, 3, 4]


def test_bit_manipulation_subsets_lazy():
    api = _import("algorithms.bit_manipulation.subsets", "subsets")
    result = api([1, 2, 3])
    assert not isinstance(result, (list, tuple, set, frozenset, dict, str))
    assert iter(result) is result
    assert set(result) == {
        (), (1,), (2,), (3,), (1, 2), (1, 3), (2, 3), (1, 2, 3),
    }


def test_sliding_window_unified_edge_semantics():
    heap_msw = _import("algorithms.heap.sliding_window_max", "max_sliding_window")
    queue_msw = _import("algorithms.queue.max_sliding_window", "max_sliding_window")
    for variant in (heap_msw, queue_msw):
        # k == len(input): exactly one window
        assert list(variant(NUMS, len(NUMS))) == [max(NUMS)]
        # k > len(input): no complete window
        assert list(variant(NUMS, len(NUMS) + 1)) == []
        # empty input
        assert list(variant([], 3)) == []
        # non-positive k raises eagerly, at call time
        with pytest.raises(ValueError):
            variant(NUMS, 0)
        with pytest.raises(ValueError):
            variant(NUMS, -3)


def test_fizzbuzz_validation_is_eager():
    from algorithms.string.fizzbuzz import fizzbuzz

    with pytest.raises(ValueError):
        fizzbuzz(0)
    with pytest.raises(ValueError):
        fizzbuzz(-2)


def test_eager_apis_stay_materialized():
    from algorithms.array.merge_intervals import merge_intervals
    from algorithms.array.top_1 import top_1
    from algorithms.graph.find_all_cliques import find_all_cliques
    from algorithms.heap.k_closest_points import k_closest
    from algorithms.sorting.merge_sort import merge_sort
    from algorithms.sorting.quick_sort import quick_sort
    from algorithms.string.group_anagrams import group_anagrams
    from algorithms.tree.binary_tree_views import bottom_view, top_view

    assert isinstance(merge_sort([38, 27, 43, 3, 9, 82, 10]), list)
    assert merge_sort([38, 27, 43, 3, 9, 82, 10]) == GOLDEN["merge_sort"]
    assert isinstance(quick_sort([38, 27, 43, 3, 9, 82, 10]), list)

    intervals = merge_intervals([[1, 3], [2, 6], [8, 10], [15, 18]])
    assert isinstance(intervals, list)
    assert _canon_items(intervals) == _canon_items(GOLDEN["merge_intervals"])

    assert isinstance(top_1([1, 1, 2, 2, 2, 3]), list)
    assert top_1([1, 1, 2, 2, 2, 3]) == GOLDEN["top_1"]

    groups = group_anagrams(["eat", "tea", "tan", "ate", "nat", "bat"])
    assert isinstance(groups, list)
    assert groups == GOLDEN["group_anagrams"]

    edges = {
        "A": {"B", "C"},
        "B": {"A", "C"},
        "C": {"A", "B"},
        "D": {"E"},
        "E": {"D"},
        "F": set(),
    }
    cliques = find_all_cliques(edges)
    assert isinstance(cliques, list)
    assert _canon_items(cliques) == _canon_items(GOLDEN["find_all_cliques"])

    closest = k_closest([(1, 2), (3, 4), (5, 6), (7, 8)], 2, (0, 0))
    assert isinstance(closest, list)
    assert _canon_items(closest) == _canon_items(GOLDEN["k_closest"])

    assert isinstance(top_view(build_view_tree()), list)
    assert top_view(build_view_tree()) == GOLDEN["top_view"]
    assert isinstance(bottom_view(build_view_tree()), list)
    assert bottom_view(build_view_tree()) == GOLDEN["bottom_view"]


def _first_item_worker(module, func, args, queue):
    try:
        sys.path.insert(0, "/app")
        import importlib
        import time as timer

        api = getattr(importlib.import_module(module), func)
        start = timer.perf_counter()
        iterator = api(*args)
        first = next(iterator)
        queue.put(("ok", timer.perf_counter() - start, repr(first)[:80]))
    except Exception as exc:  # noqa: BLE001
        queue.put(("error", 0.0, repr(exc)))


@pytest.mark.parametrize(
    "module,func,args",
    [
        ("algorithms.backtracking.permute", "permute", (list(range(11)),)),
        ("algorithms.backtracking.generate_parenthesis", "generate_parenthesis_v1", (15,)),
        ("algorithms.backtracking.subsets", "subsets", (list(range(24)),)),
        ("algorithms.math.generate_strobogrammtic", "gen_strobogrammatic", (20,)),
        ("algorithms.backtracking.letter_combination", "letter_combinations", ("9" * 12,)),
    ],
    ids=["permute", "generate_parenthesis_v1", "subsets", "gen_strobogrammatic", "letter_combinations"],
)
def test_first_item_is_lazy(module, func, args):
    """The first item must be produced without computing the whole
    (astronomically large) result set. Runs in a subprocess so an eager
    implementation is killed instead of stalling the verifier."""
    queue = multiprocessing.Queue()
    proc = multiprocessing.Process(
        target=_first_item_worker, args=(module, func, args, queue)
    )
    proc.start()
    proc.join(30)
    if proc.is_alive():
        proc.terminate()
        proc.join()
        pytest.fail(
            f"{module}.{func}: first item not produced within 30s; "
            "results must be produced lazily"
        )
    status, elapsed, payload = queue.get()
    assert status == "ok", f"{module}.{func} raised {payload}"
    assert elapsed < 15.0, f"{module}.{func}: first item took {elapsed:.1f}s"


def test_repository_test_suite_still_passes():
    """The repository's own test suite under /app/tests must pass,
    guarding against regressions in untouched behavior."""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"],
        cwd="/app",
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert proc.returncode == 0, (
        "repository test suite failed:\n" + proc.stdout[-4000:] + proc.stderr[-2000:]
    )
