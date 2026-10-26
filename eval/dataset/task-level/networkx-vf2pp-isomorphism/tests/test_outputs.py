#!/usr/bin/env python3
"""Verify the VF2++ graph-isomorphism feature and the accompanying
touch-ups of the classic high-level isomorphism API in /app.

The ground truth used here is frozen inside this file: small graphs are
validated against an independent brute-force permutation enumeration, and
larger cases use hard-coded, mathematically known automorphism counts or
directly asserted return values.  Nothing is recomputed from the
repository state at judgement time.
"""

import inspect
import itertools
import sys
import traceback

sys.path.insert(0, "/app")

FAILURES = []


def check(name, fn):
    try:
        fn()
    except Exception:
        FAILURES.append(name)
        print(f"FAIL {name}")
        traceback.print_exc(limit=3)
    else:
        print(f"ok   {name}")


# ---------------------------------------------------------------------------
# Independent brute-force ground truth (works on Graph/DiGraph/MultiGraph/
# MultiDiGraph for small node counts).
# ---------------------------------------------------------------------------

def _label(G, u, attr, default):
    return G.nodes[u].get(attr, default)


def brute_force_isomorphisms(G1, G2, node_label=None, default_label=None):
    n1, n2 = list(G1.nodes), list(G2.nodes)
    if len(n1) != len(n2):
        return []
    out = []
    for perm in itertools.permutations(n2):
        p = dict(zip(n1, perm))
        if node_label is not None and any(
            _label(G1, u, node_label, default_label)
            != _label(G2, p[u], node_label, default_label)
            for u in n1
        ):
            continue
        ok = True
        for u in n1:
            for v in n1:
                if G1.is_multigraph():
                    same = G1.number_of_edges(u, v) == G2.number_of_edges(p[u], p[v])
                else:
                    same = G1.has_edge(u, v) == G2.has_edge(p[u], p[v])
                if not same:
                    ok = False
                    break
            if not ok:
                break
        if ok:
            out.append(p)
    return out


def frozen(mapping):
    return frozenset(mapping.items())


def assert_full_enumeration(nx, G1, G2, node_label=None, default_label=None):
    """vf2pp_all_isomorphisms must exactly reproduce the brute-force set."""
    expected = {frozen(m) for m in brute_force_isomorphisms(G1, G2, node_label, default_label)}
    got_list = list(nx.vf2pp_all_isomorphisms(G1, G2, node_label=node_label,
                                              default_label=default_label))
    got = {frozen(m) for m in got_list}
    assert len(got_list) == len(got), "duplicate mappings yielded"
    assert got == expected, (
        f"enumeration mismatch: expected {len(expected)} mappings, got {len(got)}"
    )
    assert nx.vf2pp_is_isomorphic(G1, G2, node_label=node_label,
                                  default_label=default_label) == bool(expected)
    one = nx.vf2pp_isomorphism(G1, G2, node_label=node_label, default_label=default_label)
    if expected:
        assert isinstance(one, dict)
        assert frozen(one) in expected
    else:
        assert one is None


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def test_api_surface(nx):
    for name in ("vf2pp_is_isomorphic", "vf2pp_isomorphism", "vf2pp_all_isomorphisms"):
        assert callable(getattr(nx, name, None)), f"nx.{name} missing"
    for name in ("vf2pp_is_isomorphic", "vf2pp_isomorphism", "vf2pp_all_isomorphisms"):
        sig = inspect.signature(getattr(nx, name))
        params = list(sig.parameters)
        assert params[:2] == ["G1", "G2"], f"{name} signature: {params}"
        assert "node_label" in sig.parameters
        assert sig.parameters["node_label"].default is None
        assert "default_label" in sig.parameters
        assert sig.parameters["default_label"].default is None


def test_return_types(nx):
    G = nx.path_graph(4)
    H = nx.relabel_nodes(nx.path_graph(4), {0: "a", 1: "b", 2: "c", 3: "d"})
    r = nx.vf2pp_is_isomorphic(G, H)
    assert isinstance(r, bool), f"expected bool, got {type(r)}"
    assert r is True
    m = nx.vf2pp_isomorphism(G, H)
    assert isinstance(m, dict)
    assert set(m) == set(G.nodes)
    assert set(m.values()) == set(H.nodes)
    it = nx.vf2pp_all_isomorphisms(G, H)
    assert iter(it) is iter(it), "expected an iterator/generator"
    mappings = list(it)
    assert len(mappings) == 2  # P4 has exactly 2 automorphisms
    assert all(set(mm) == set(G.nodes) and set(mm.values()) == set(H.nodes) for mm in mappings)


def test_mapping_is_valid_isomorphism(nx):
    # every yielded mapping must be an actual edge-preserving bijection
    G1 = nx.cycle_graph(6)
    G2 = nx.relabel_nodes(nx.cycle_graph(6), {i: (i * 7 + 3) % 13 for i in range(6)})
    for m in nx.vf2pp_all_isomorphisms(G1, G2):
        assert len(set(m.values())) == len(m), "not a bijection"
        for u, v in G1.edges:
            assert G2.has_edge(m[u], m[v])


def test_undirected_enumeration(nx):
    assert_full_enumeration(nx, nx.path_graph(4), nx.path_graph(4))           # 2
    assert_full_enumeration(nx, nx.cycle_graph(6), nx.cycle_graph(6))         # 12
    assert_full_enumeration(nx, nx.path_graph(5), nx.cycle_graph(5))          # 0
    assert_full_enumeration(nx, nx.complete_graph(4), nx.complete_graph(4))   # 24
    assert_full_enumeration(nx, nx.star_graph(4), nx.star_graph(4))           # 24
    # isomorphic pair with permuted labels
    A = nx.Graph([(0, 1), (1, 2), (2, 3), (3, 0), (0, 2)])
    B = nx.relabel_nodes(A, {0: 5, 1: 6, 2: 7, 3: 8})
    assert_full_enumeration(nx, A, B)


def test_directed_enumeration(nx):
    # directed cycle: direction matters -> 6 automorphisms, not 12
    C6d = nx.cycle_graph(6, create_using=nx.DiGraph)
    assert_full_enumeration(nx, C6d, C6d.copy())
    # a reversed edge breaks the in/out degree sequence -> no longer isomorphic
    broken = C6d.copy()
    broken.remove_edge(0, 1)
    broken.add_edge(1, 0)
    assert nx.vf2pp_is_isomorphic(C6d, broken) is False
    G1 = nx.DiGraph([(0, 1), (1, 2), (2, 0), (3, 2)])
    G2 = nx.DiGraph([(0, 1), (1, 2), (2, 0), (2, 3)])
    assert nx.vf2pp_is_isomorphic(G1, G2) is False
    assert nx.vf2pp_isomorphism(G1, G2) is None
    assert list(nx.vf2pp_all_isomorphisms(G1, G2)) == []
    H2 = nx.relabel_nodes(G1, {0: 10, 1: 11, 2: 12, 3: 13})
    assert_full_enumeration(nx, G1, H2)


def test_multigraph_enumeration(nx):
    MG = nx.MultiGraph()
    MG.add_edges_from([(0, 1), (0, 1), (1, 2), (0, 2)])
    assert_full_enumeration(nx, MG, MG.copy())  # 2 automorphisms
    # parallel-edge multiplicity must be respected
    MG2 = nx.MultiGraph()
    MG2.add_edges_from([(0, 1), (1, 2), (1, 2), (0, 2)])
    assert nx.vf2pp_is_isomorphic(MG, MG2) is True  # same up to relabeling
    MG3 = nx.MultiGraph()
    MG3.add_edges_from([(0, 1), (0, 1), (0, 1), (1, 2), (0, 2)])
    assert nx.vf2pp_is_isomorphic(MG, MG3) is False
    MD = nx.MultiDiGraph()
    MD.add_edges_from([(0, 1), (0, 1), (1, 2), (2, 0), (2, 0)])
    assert_full_enumeration(nx, MD, MD.copy())  # 1 automorphism


def test_node_labels(nx):
    def labeled(G, attrs):
        G = G.copy()
        nx.set_node_attributes(G, attrs, "label")
        return G

    P = nx.path_graph(4)
    aabb = labeled(P, {0: "a", 1: "a", 2: "b", 3: "b"})
    bbaa = labeled(P, {0: "b", 1: "b", 2: "a", 3: "a"})
    abba = labeled(P, {0: "a", 1: "b", 2: "b", 3: "a"})

    # structurally all isomorphic
    assert nx.vf2pp_is_isomorphic(aabb, bbaa) is True
    # label-constrained counts frozen from brute force
    assert_full_enumeration(nx, aabb, aabb.copy(), node_label="label")   # 1
    assert_full_enumeration(nx, aabb, bbaa, node_label="label")          # 1
    assert_full_enumeration(nx, abba, abba.copy(), node_label="label")   # 2
    assert nx.vf2pp_is_isomorphic(aabb, abba, node_label="label") is False
    # every label-respecting mapping must map same labels together
    for m in nx.vf2pp_all_isomorphisms(aabb, bbaa, node_label="label"):
        for u, v in m.items():
            assert aabb.nodes[u]["label"] == bbaa.nodes[v]["label"]


def test_default_label(nx):
    G1 = nx.path_graph(3)
    G1.nodes[0]["label"] = "x"
    G2 = nx.path_graph(3)
    G2.nodes[2]["label"] = "x"
    G3 = nx.path_graph(3)
    G3.nodes[1]["label"] = "x"
    # nodes without the attribute fall back to default_label
    assert nx.vf2pp_is_isomorphic(G1, G2, node_label="label", default_label=None) is True
    assert nx.vf2pp_is_isomorphic(G1, G3, node_label="label", default_label=None) is False
    # changing the default can change the outcome
    G4 = nx.path_graph(3)
    nx.set_node_attributes(G4, {0: "x", 1: "y", 2: "y"}, "label")
    G5 = nx.path_graph(3)
    nx.set_node_attributes(G5, {2: "x"}, "label")
    assert nx.vf2pp_is_isomorphic(G4, G5, node_label="label", default_label="y") is True
    assert nx.vf2pp_is_isomorphic(G4, G5, node_label="label", default_label=None) is False


def test_quick_negatives(nx):
    # node-count mismatch
    assert nx.vf2pp_is_isomorphic(nx.path_graph(3), nx.path_graph(4)) is False
    assert nx.vf2pp_isomorphism(nx.path_graph(3), nx.path_graph(4)) is None
    assert list(nx.vf2pp_all_isomorphisms(nx.path_graph(3), nx.path_graph(4))) == []
    # degree-sequence mismatch, same node count
    assert nx.vf2pp_is_isomorphic(nx.path_graph(5), nx.star_graph(4)) is False
    # single-node and empty graphs
    assert nx.vf2pp_is_isomorphic(nx.path_graph(1), nx.path_graph(1)) is True
    assert nx.vf2pp_is_isomorphic(nx.Graph(), nx.path_graph(1)) is False


def test_frozen_automorphism_counts(nx):
    # mathematically fixed values; no recomputation from repo state
    cases = [
        (nx.path_graph(4), nx.path_graph(4), 2),
        (nx.cycle_graph(6), nx.cycle_graph(6), 12),
        (nx.cycle_graph(6, create_using=nx.DiGraph),
         nx.cycle_graph(6, create_using=nx.DiGraph), 6),
        (nx.complete_graph(5), nx.complete_graph(5), 120),
        (nx.petersen_graph(), nx.petersen_graph(), 120),
    ]
    for G1, G2, count in cases:
        got = list(nx.vf2pp_all_isomorphisms(G1, G2))
        assert len(got) == count, f"expected {count}, got {len(got)}"
        assert len({frozen(m) for m in got}) == count, "duplicate mappings"


def test_no_recursion_limit_fragility(nx):
    # non-recursive implementation: long paths must not hit recursion limits
    n = 5000
    G1 = nx.path_graph(n)
    # (3i + 1) mod 5000 is a bijection because gcd(3, 5000) == 1
    G2 = nx.relabel_nodes(nx.path_graph(n), {i: (i * 3 + 1) % n for i in range(n)})
    assert nx.vf2pp_is_isomorphic(G1, G2) is True
    m = nx.vf2pp_isomorphism(G1, G2)
    assert m is not None and len(m) == n


# ---------------------------------------------------------------------------
# Contract additions on the top-level API (previously-broken behaviour)
# ---------------------------------------------------------------------------

def test_could_be_isomorphic_handles_all_graph_types(nx):
    # DiGraph: previously raised NetworkXNotImplemented via triangles().
    Cd = nx.cycle_graph(6, create_using=nx.DiGraph)
    assert nx.could_be_isomorphic(Cd, Cd) is True
    assert nx.fast_could_be_isomorphic(Cd, Cd) is True
    assert nx.faster_could_be_isomorphic(Cd, Cd) is True
    # A different DiGraph with the same node count but different degree
    # sequence must still be rejected -- the pre-check must remain a real
    # necessary condition, not a rubber stamp that returns True unconditionally.
    Cd_diff = nx.DiGraph([(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)])
    assert nx.could_be_isomorphic(Cd, Cd_diff) is False
    assert nx.fast_could_be_isomorphic(Cd, Cd_diff) is False
    assert nx.faster_could_be_isomorphic(Cd, Cd_diff) is False

    # MultiGraph: previously raised NetworkXNotImplemented on triangles/cliques.
    MG = nx.MultiGraph()
    MG.add_edges_from([(0, 1), (0, 1), (1, 2), (0, 2)])
    assert nx.could_be_isomorphic(MG, MG) is True
    assert nx.fast_could_be_isomorphic(MG, MG) is True
    assert nx.faster_could_be_isomorphic(MG, MG) is True
    MG_diff = nx.MultiGraph()
    MG_diff.add_edges_from([(0, 1), (0, 1), (0, 1), (1, 2), (0, 2)])
    assert nx.could_be_isomorphic(MG, MG_diff) is False
    assert nx.fast_could_be_isomorphic(MG, MG_diff) is False

    # MultiDiGraph: same story on the directed side.
    MD = nx.MultiDiGraph()
    MD.add_edges_from([(0, 1), (0, 1), (1, 2), (2, 0), (2, 0)])
    assert nx.could_be_isomorphic(MD, MD) is True
    assert nx.fast_could_be_isomorphic(MD, MD) is True

    # Cross-type comparisons: incompatible directedness / multi-status can
    # never yield an isomorphism, so the pre-check should short-circuit to
    # False instead of relying on the underlying degree stats.
    G_simple = nx.Graph([(0, 1), (1, 2), (2, 0)])
    D_simple = nx.DiGraph([(0, 1), (1, 2), (2, 0)])
    M_undir = nx.MultiGraph([(0, 1), (1, 2), (2, 0)])
    assert nx.could_be_isomorphic(G_simple, D_simple) is False
    assert nx.fast_could_be_isomorphic(G_simple, D_simple) is False
    assert nx.faster_could_be_isomorphic(G_simple, D_simple) is False
    assert nx.could_be_isomorphic(G_simple, M_undir) is False
    assert nx.faster_could_be_isomorphic(G_simple, M_undir) is False


def test_could_be_isomorphic_regressions_on_undirected_simple(nx):
    # Legacy assertions from the pre-existing test_isomorphism.py suite:
    # the undirected-simple branch must still exercise the full
    # degree/triangle/clique property comparison and reject non-matching
    # graphs.  These calls previously succeeded before the refactor and
    # must continue to do so.
    G1 = nx.Graph([(1, 2), (1, 3), (1, 5), (2, 3)])
    G2 = nx.Graph([(10, 20), (20, 30), (10, 30), (10, 50)])
    G3 = nx.Graph([(1, 2), (1, 3), (1, 5), (2, 5)])
    G4 = nx.Graph([(1, 2), (1, 3), (1, 5), (2, 4)])
    G5 = nx.Graph([(1, 2), (1, 3)])
    G6 = nx.Graph([(10, 20), (20, 30), (10, 30), (10, 50), (20, 50)])
    assert nx.could_be_isomorphic(G1, G2) is True
    assert nx.could_be_isomorphic(G1, G3) is True
    assert nx.could_be_isomorphic(G1, G4) is False
    assert nx.could_be_isomorphic(G3, G2) is True
    assert nx.could_be_isomorphic(G1, G6) is False
    assert nx.fast_could_be_isomorphic(G3, G2) is True
    assert nx.fast_could_be_isomorphic(G3, G5) is False
    assert nx.fast_could_be_isomorphic(G1, G6) is False
    assert nx.faster_could_be_isomorphic(G3, G2) is True
    assert nx.faster_could_be_isomorphic(G3, G5) is False
    assert nx.faster_could_be_isomorphic(G1, G6) is False


def test_is_isomorphic_node_label_routes_to_vf2pp(nx):
    # The label-only entry point must behave identically to
    # nx.vf2pp_is_isomorphic across every graph type.
    P = nx.path_graph(4)
    nx.set_node_attributes(P, {0: "a", 1: "b", 2: "b", 3: "a"}, "kind")
    Q = nx.path_graph(4)
    nx.set_node_attributes(Q, {0: "a", 1: "b", 2: "b", 3: "a"}, "kind")
    assert nx.is_isomorphic(P, Q, node_label="kind") is True
    R = nx.path_graph(4)
    nx.set_node_attributes(R, {0: "a", 1: "a", 2: "b", 3: "b"}, "kind")
    assert nx.is_isomorphic(P, R, node_label="kind") is False

    # DiGraph: label-aware on a directed 3-cycle.
    Cd = nx.DiGraph([(0, 1), (1, 2), (2, 0)])
    nx.set_node_attributes(Cd, {0: "x", 1: "y", 2: "z"}, "role")
    Cd2 = nx.DiGraph([(10, 20), (20, 30), (30, 10)])
    nx.set_node_attributes(Cd2, {10: "x", 20: "y", 30: "z"}, "role")
    assert nx.is_isomorphic(Cd, Cd2, node_label="role") is True
    Cd_bad = nx.DiGraph([(10, 20), (20, 30), (30, 10)])
    nx.set_node_attributes(Cd_bad, {10: "y", 20: "x", 30: "z"}, "role")
    assert nx.is_isomorphic(Cd, Cd_bad, node_label="role") is False

    # MultiGraph with parallel-edge multiplicity + labels.
    MG = nx.MultiGraph()
    MG.add_edges_from([(0, 1), (0, 1), (1, 2), (0, 2)])
    nx.set_node_attributes(MG, {0: "r", 1: "s", 2: "t"}, "kind")
    MG_relabel = nx.relabel_nodes(MG, {0: 100, 1: 200, 2: 300})
    assert nx.is_isomorphic(MG, MG_relabel, node_label="kind") is True

    # default_label should feed through when the attribute is missing.
    A = nx.path_graph(3)
    nx.set_node_attributes(A, {0: "x", 1: "y", 2: "y"}, "kind")
    B = nx.path_graph(3)
    nx.set_node_attributes(B, {2: "x"}, "kind")
    assert nx.is_isomorphic(A, B, node_label="kind", default_label="y") is True
    assert nx.is_isomorphic(A, B, node_label="kind") is False


def test_is_isomorphic_node_label_composes_with_node_match(nx):
    # When node_label is combined with a user-supplied node_match, both
    # constraints must be enforced simultaneously.  The label constraint
    # must NOT silently override or be overridden by node_match.
    G = nx.path_graph(4)
    nx.set_node_attributes(G, {0: "a", 1: "b", 2: "b", 3: "a"}, "kind")
    nx.set_node_attributes(G, {0: 1, 1: 2, 2: 3, 3: 4}, "w")
    H = nx.path_graph(4)
    nx.set_node_attributes(H, {0: "a", 1: "b", 2: "b", 3: "a"}, "kind")
    nx.set_node_attributes(H, {0: 1, 1: 2, 2: 3, 3: 4}, "w")

    # With matching label constraint AND matching node_match on 'w', OK.
    assert nx.is_isomorphic(
        G, H, node_label="kind",
        node_match=lambda a, b: a.get("w") == b.get("w"),
    ) is True

    # If node_match is stricter (differing weights), overall must be False.
    H_wdiff = H.copy()
    nx.set_node_attributes(H_wdiff, {0: 99}, "w")
    assert nx.is_isomorphic(
        G, H_wdiff, node_label="kind",
        node_match=lambda a, b: a.get("w") == b.get("w"),
    ) is False

    # If label is stricter (differing labels), overall must be False.
    H_ldiff = H.copy()
    nx.set_node_attributes(H_ldiff, {0: "b", 3: "b"}, "kind")
    assert nx.is_isomorphic(
        G, H_ldiff, node_label="kind",
        node_match=lambda a, b: a.get("w") == b.get("w"),
    ) is False


def test_is_isomorphic_legacy_signature_preserved(nx):
    # Positional call (G1, G2) with no keyword args unchanged.
    assert nx.is_isomorphic(nx.path_graph(4), nx.path_graph(4)) is True
    # node_match / edge_match keyword-only usage unchanged.
    G = nx.DiGraph()
    nx.add_path(G, [1, 2, 3, 4], weight=1)
    H = nx.DiGraph()
    nx.add_path(H, [10, 20, 30, 40], weight=2)
    assert nx.is_isomorphic(G, H) is True
    assert nx.is_isomorphic(
        G, H, edge_match=lambda a, b: a.get("weight") == b.get("weight")
    ) is False

    # Mixed directedness must still raise NetworkXError; do NOT silently
    # coerce or return False.
    try:
        nx.is_isomorphic(nx.Graph([(1, 2)]), nx.DiGraph([(1, 2)]))
    except nx.NetworkXError:
        pass
    else:
        raise AssertionError("expected NetworkXError on directed/undirected mismatch")

    # Signature must have node_label & default_label as keyword-only optional
    # additions, without shifting node_match / edge_match.
    sig = inspect.signature(nx.is_isomorphic)
    params = list(sig.parameters)
    assert params[:4] == ["G1", "G2", "node_match", "edge_match"], (
        f"is_isomorphic signature changed: {params}"
    )
    assert "node_label" in sig.parameters and sig.parameters["node_label"].default is None
    assert "default_label" in sig.parameters and sig.parameters["default_label"].default is None


def test_vf2pp_matches_is_isomorphic_on_labels(nx):
    # Guard: both entry points must agree on structurally identical inputs.
    # Frozen expected values (True/False) so the test cannot silently pass
    # by comparing two broken implementations to each other.
    frozen_cases = [
        (nx.path_graph(6), nx.path_graph(6), None, True),
        (nx.path_graph(6), nx.cycle_graph(6), None, False),
        (nx.cycle_graph(6, create_using=nx.DiGraph),
         nx.cycle_graph(6, create_using=nx.DiGraph), None, True),
    ]
    for G1, G2, lbl, expected in frozen_cases:
        assert nx.vf2pp_is_isomorphic(G1, G2, node_label=lbl) is expected
        assert nx.is_isomorphic(G1, G2, node_label=lbl) is expected if lbl else True
        if lbl is None:
            assert nx.is_isomorphic(G1, G2) is expected


def test_could_be_isomorphic_with_node_label(nx):
    # Label multiset must be respected when node_label is supplied to the
    # pre-check helpers.  When it is not supplied the helpers must ignore
    # node attributes entirely (the classical behaviour).
    G = nx.path_graph(4)
    nx.set_node_attributes(G, {0: "a", 1: "b", 2: "b", 3: "a"}, "kind")
    H_same = nx.path_graph(4)
    nx.set_node_attributes(H_same, {0: "a", 1: "b", 2: "b", 3: "a"}, "kind")
    H_perm = nx.path_graph(4)
    nx.set_node_attributes(H_perm, {0: "b", 1: "a", 2: "a", 3: "b"}, "kind")
    H_diff = nx.path_graph(4)
    nx.set_node_attributes(H_diff, {0: "a", 1: "a", 2: "a", 3: "b"}, "kind")

    # Same multiset {a,a,b,b} in both -> pre-check may return True.
    assert nx.could_be_isomorphic(G, H_same, node_label="kind") is True
    assert nx.could_be_isomorphic(G, H_perm, node_label="kind") is True
    assert nx.fast_could_be_isomorphic(G, H_same, node_label="kind") is True
    assert nx.faster_could_be_isomorphic(G, H_same, node_label="kind") is True

    # Different multiset {a,a,b,b} vs {a,a,a,b} -> must be rejected.
    assert nx.could_be_isomorphic(G, H_diff, node_label="kind") is False
    assert nx.fast_could_be_isomorphic(G, H_diff, node_label="kind") is False
    assert nx.faster_could_be_isomorphic(G, H_diff, node_label="kind") is False

    # Without node_label, label mismatch is invisible to the pre-checks
    # (structural properties still match).
    assert nx.could_be_isomorphic(G, H_diff) is True
    assert nx.fast_could_be_isomorphic(G, H_diff) is True
    assert nx.faster_could_be_isomorphic(G, H_diff) is True

    # default_label must be honoured.  G has kind on every node; K only
    # labels its two endpoints, but with default_label="b" the missing
    # middle nodes fall back to "b" so the multisets coincide with G's
    # {a, a, b, b}.
    K = nx.path_graph(4)
    nx.set_node_attributes(K, {0: "a", 3: "a"}, "kind")
    assert nx.could_be_isomorphic(G, K, node_label="kind", default_label="b") is True
    assert nx.faster_could_be_isomorphic(G, K, node_label="kind", default_label="b") is True
    # Without the default the missing nodes read as None and the check
    # must fail (multiset {a, a, None, None} != {a, a, b, b}).
    assert nx.could_be_isomorphic(G, K, node_label="kind") is False
    assert nx.faster_could_be_isomorphic(G, K, node_label="kind") is False


def test_graphmatcher_class_still_works(nx):
    # The existing VF2 class-level API must remain untouched; downstream
    # code that instantiates GraphMatcher / DiGraphMatcher directly should
    # not have changed observable behaviour.
    GM = nx.algorithms.isomorphism.GraphMatcher
    DM = nx.algorithms.isomorphism.DiGraphMatcher
    G = nx.path_graph(4)
    H = nx.relabel_nodes(nx.path_graph(4), {0: 10, 1: 11, 2: 12, 3: 13})
    m = GM(G, H)
    assert m.is_isomorphic() is True
    mapping = m.mapping
    assert set(mapping) == set(G.nodes)
    assert set(mapping.values()) == set(H.nodes)
    Cd = nx.cycle_graph(6, create_using=nx.DiGraph)
    Cd2 = Cd.copy()
    dm = DM(Cd, Cd2)
    assert dm.is_isomorphic() is True


def main():
    try:
        import networkx as nx
    except Exception:
        traceback.print_exc()
        print("could not import networkx from /app")
        return 1

    checks = [
        ("api_surface", lambda: test_api_surface(nx)),
        ("return_types", lambda: test_return_types(nx)),
        ("mapping_is_valid_isomorphism", lambda: test_mapping_is_valid_isomorphism(nx)),
        ("undirected_enumeration", lambda: test_undirected_enumeration(nx)),
        ("directed_enumeration", lambda: test_directed_enumeration(nx)),
        ("multigraph_enumeration", lambda: test_multigraph_enumeration(nx)),
        ("node_labels", lambda: test_node_labels(nx)),
        ("default_label", lambda: test_default_label(nx)),
        ("quick_negatives", lambda: test_quick_negatives(nx)),
        ("frozen_automorphism_counts", lambda: test_frozen_automorphism_counts(nx)),
        ("no_recursion_limit_fragility", lambda: test_no_recursion_limit_fragility(nx)),
        ("could_be_isomorphic_handles_all_graph_types",
         lambda: test_could_be_isomorphic_handles_all_graph_types(nx)),
        ("could_be_isomorphic_regressions_on_undirected_simple",
         lambda: test_could_be_isomorphic_regressions_on_undirected_simple(nx)),
        ("is_isomorphic_node_label_routes_to_vf2pp",
         lambda: test_is_isomorphic_node_label_routes_to_vf2pp(nx)),
        ("is_isomorphic_node_label_composes_with_node_match",
         lambda: test_is_isomorphic_node_label_composes_with_node_match(nx)),
        ("is_isomorphic_legacy_signature_preserved",
         lambda: test_is_isomorphic_legacy_signature_preserved(nx)),
        ("vf2pp_matches_is_isomorphic_on_labels",
         lambda: test_vf2pp_matches_is_isomorphic_on_labels(nx)),
        ("could_be_isomorphic_with_node_label",
         lambda: test_could_be_isomorphic_with_node_label(nx)),
        ("graphmatcher_class_still_works",
         lambda: test_graphmatcher_class_still_works(nx)),
    ]
    for name, fn in checks:
        check(name, fn)

    print(f"\n{len(checks) - len(FAILURES)}/{len(checks)} checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
