"""Verifier checks for the semantic clustering & deduplication toolkit task.

All expectations are frozen golden values computed once from the reference
specification (cosine distances, seeded k-means++, union-find deduplication,
contingency-table clustering metrics). Nothing here reads expected values from
the repository state: every assertion compares the *behavior* of the installed
`sentence_transformers` package against constants embedded in this file.

The checks run fully offline on CPU and need no model downloads.
"""

from __future__ import annotations

import csv
import math

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Fixtures (frozen inputs)
# ---------------------------------------------------------------------------

# Three well-separated blobs (4 points each), separable under both cosine and
# Euclidean distance, no zero vectors.
BLOB_POINTS = [
    [1.0, 0.0],
    [1.0, 0.05],
    [0.95, 0.0],
    [0.95, 0.05],
    [0.0, 1.0],
    [0.05, 1.0],
    [0.0, 0.95],
    [0.05, 0.95],
    [-1.0, 0.0],
    [-1.0, 0.05],
    [-0.95, 0.0],
    [-0.95, 0.05],
]
BLOB_GOLDEN_LABELS = [0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2]

# 1D chain where single and complete linkage disagree (frozen goldens below).
CHAIN_POINTS = [[0.0], [0.9], [2.0], [2.9], [4.1]]

# Contingency-table metric fixture with frozen golden values.
GOLD_LABELS = [0, 0, 0, 1, 1, 1, 2, 2, 2, 2]
PRED_LABELS = [0, 0, 1, 1, 1, 2, 2, 2, 3, 3]
GOLDEN_V_MEASURE = 0.5759466679735673
GOLDEN_HOMOGENEITY = 0.6492712741077913
GOLDEN_COMPLETENESS = 0.5175031261015375
GOLDEN_NMI = 0.5759466679735676
GOLDEN_ARI = 0.23728813559322032
GOLDEN_FM = 0.4082482904638631
GOLDEN_PURITY = 0.8
GOLDEN_ACCURACY = 0.6

# Evaluator fixtures.
SENTENCES = ["a1", "a2", "a3", "b1", "b2", "b3"]
LABELS = ["fruit", "fruit", "fruit", "animal", "animal", "animal"]
CLEAN_EMBEDDINGS = [
    [1.0, 0.0],
    [1.0, 0.05],
    [0.95, 0.0],
    [0.0, 1.0],
    [0.05, 1.0],
    [0.0, 0.95],
]
# "a3" and "b3" embeddings swapped -> imperfect clustering, frozen goldens:
MIXED_EMBEDDINGS = [
    [1.0, 0.0],
    [1.0, 0.05],
    [0.0, 0.95],
    [0.0, 1.0],
    [0.05, 1.0],
    [0.95, 0.0],
]
GOLDEN_MIXED_V_MEASURE = 0.08170416594551055
GOLDEN_MIXED_NMI = 0.0817041659455104
GOLDEN_MIXED_ARI = -0.11111111111111108
GOLDEN_MIXED_ACCURACY = 2.0 / 3.0

TOL = 1e-9


class StubModel:
    """Minimal stand-in for SentenceTransformer: encode() returns fixed embeddings."""

    def __init__(self, embeddings):
        self.embeddings = np.asarray(embeddings, dtype=np.float64)
        self.encode_calls = []

    def encode(self, sentences, batch_size=32, show_progress_bar=False, convert_to_numpy=True, **kwargs):
        self.encode_calls.append({"sentences": list(sentences), "batch_size": batch_size})
        return self.embeddings[: len(sentences)]

    class _ModelCardData:
        def set_evaluation_metrics(self, evaluator, metrics, epoch, step):
            pass

    model_card_data = _ModelCardData()


def as_partition(result) -> set:
    return {frozenset(indices) for indices in result.clusters.values()}


def clustering_from_labels(labels):
    """Build a ClusteringResult from labels without assuming the constructor signature.

    The spec pins only the attribute surface (``.labels``, ``.clusters``,
    ``.cluster_sizes``, ``.num_clusters``) of ``ClusteringResult``, not how it is
    constructed, so try the plausible construction paths in order: a labels-only
    constructor, a ``from_labels`` classmethod, then a full keyword construction
    using the spec-pinned attribute names.
    """
    from sentence_transformers.util import ClusteringResult

    labels = list(labels)
    try:
        return ClusteringResult(labels=labels)
    except TypeError:
        pass
    from_labels = getattr(ClusteringResult, "from_labels", None)
    if callable(from_labels):
        return from_labels(labels)
    clusters: dict[int, list[int]] = {}
    for idx, cluster_id in enumerate(labels):
        clusters.setdefault(cluster_id, []).append(idx)
    return ClusteringResult(
        labels=labels,
        clusters=clusters,
        cluster_sizes={cluster_id: len(members) for cluster_id, members in clusters.items()},
        num_clusters=len(clusters),
    )


# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------


def test_util_functions_importable():
    from sentence_transformers.util import (  # noqa: F401
        ClusteringResult,
        DeduplicationResult,
        adjusted_rand_score,
        agglomerative_clustering,
        cluster_accuracy_score,
        completeness_score,
        fowlkes_mallows_score,
        homogeneity_score,
        kmeans_clustering,
        normalized_mutual_info_score,
        purity_score,
        select_representatives,
        semantic_deduplication,
        v_measure_score,
    )


def test_evaluator_importable():
    from sentence_transformers.evaluation import ClusteringEvaluator  # noqa: F401
    from sentence_transformers.sentence_transformer.evaluation import (  # noqa: F401
        ClusteringEvaluator as DirectClusteringEvaluator,
    )


# ---------------------------------------------------------------------------
# k-means
# ---------------------------------------------------------------------------


def test_kmeans_frozen_blob_labels():
    from sentence_transformers.util import kmeans_clustering

    result = kmeans_clustering(BLOB_POINTS, num_clusters=3, seed=22)
    assert result.labels == BLOB_GOLDEN_LABELS
    assert result.num_clusters == 3
    assert result.cluster_sizes == {0: 4, 1: 4, 2: 4}


def test_kmeans_deterministic_and_input_type_agnostic():
    from sentence_transformers.util import kmeans_clustering

    expected = BLOB_GOLDEN_LABELS
    assert kmeans_clustering(BLOB_POINTS, num_clusters=3, seed=22).labels == expected
    assert kmeans_clustering(np.array(BLOB_POINTS), num_clusters=3, seed=22).labels == expected

    import torch

    assert kmeans_clustering(torch.tensor(BLOB_POINTS), num_clusters=3, seed=22).labels == expected


@pytest.mark.parametrize("seed", [0, 1, 7, 13, 99])
def test_kmeans_seed_invariance_on_separable_data(seed):
    from sentence_transformers.util import kmeans_clustering

    result = kmeans_clustering(BLOB_POINTS, num_clusters=3, seed=seed)
    assert result.labels == BLOB_GOLDEN_LABELS


def test_kmeans_validation():
    from sentence_transformers.util import kmeans_clustering

    for bad in (0, -1, len(BLOB_POINTS) + 1):
        with pytest.raises(ValueError):
            kmeans_clustering(BLOB_POINTS, num_clusters=bad)
    # k == n: every point its own cluster; k == 1: one cluster
    assert kmeans_clustering(BLOB_POINTS, num_clusters=len(BLOB_POINTS)).labels == list(range(len(BLOB_POINTS)))
    assert kmeans_clustering(BLOB_POINTS, num_clusters=1).labels == [0] * len(BLOB_POINTS)


def test_clustering_result_conventions():
    from sentence_transformers.util import kmeans_clustering

    result = kmeans_clustering(BLOB_POINTS, num_clusters=3, seed=22)
    # Cluster ids are normalized: the cluster containing index 0 is cluster 0,
    # ids are contiguous, and first occurrences appear in id order.
    assert result.labels[0] == 0
    assert sorted(set(result.labels)) == [0, 1, 2]
    assert result.clusters[0] == [0, 1, 2, 3]
    assert result.clusters[1] == [4, 5, 6, 7]
    assert result.clusters[2] == [8, 9, 10, 11]
    largest = result.largest_clusters(2)
    assert len(largest) == 2 and len(largest[0][1]) == 4
    assert len(result.smallest_clusters(1)) == 1


# ---------------------------------------------------------------------------
# Agglomerative clustering
# ---------------------------------------------------------------------------


def test_agglomerative_linkage_goldens():
    from sentence_transformers.util import agglomerative_clustering

    single = agglomerative_clustering(CHAIN_POINTS, num_clusters=2, linkage="single", metric="euclidean")
    assert as_partition(single) == {frozenset({0, 1, 2, 3}), frozenset({4})}

    complete = agglomerative_clustering(CHAIN_POINTS, num_clusters=2, linkage="complete", metric="euclidean")
    assert as_partition(complete) == {frozenset({0, 1}), frozenset({2, 3, 4})}

    average = agglomerative_clustering(CHAIN_POINTS, threshold=1.0, linkage="average", metric="euclidean")
    assert as_partition(average) == {frozenset({0, 1}), frozenset({2, 3}), frozenset({4})}


def test_agglomerative_blob_partition_cosine_and_euclidean():
    from sentence_transformers.util import agglomerative_clustering

    expected = {frozenset({0, 1, 2, 3}), frozenset({4, 5, 6, 7}), frozenset({8, 9, 10, 11})}
    assert as_partition(agglomerative_clustering(BLOB_POINTS, num_clusters=3, metric="cosine")) == expected
    assert as_partition(agglomerative_clustering(BLOB_POINTS, num_clusters=3, metric="euclidean")) == expected


def test_agglomerative_validation():
    from sentence_transformers.util import agglomerative_clustering

    with pytest.raises(ValueError):
        agglomerative_clustering(BLOB_POINTS)  # neither criterion
    with pytest.raises(ValueError):
        agglomerative_clustering(BLOB_POINTS, num_clusters=3, threshold=0.5)  # both
    with pytest.raises(ValueError):
        agglomerative_clustering(BLOB_POINTS, num_clusters=0)
    with pytest.raises(ValueError):
        agglomerative_clustering(BLOB_POINTS, num_clusters=len(BLOB_POINTS) + 1)
    with pytest.raises(ValueError):
        agglomerative_clustering(BLOB_POINTS, num_clusters=3, linkage="ward")
    with pytest.raises(ValueError):
        agglomerative_clustering(BLOB_POINTS, num_clusters=3, metric="manhattan")
    with pytest.raises(ValueError):
        agglomerative_clustering(BLOB_POINTS, threshold=-0.1)


# ---------------------------------------------------------------------------
# Semantic deduplication
# ---------------------------------------------------------------------------


def test_dedup_exact_duplicates_golden():
    from sentence_transformers.util import semantic_deduplication

    embeddings = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0], [1.0, 1.0]]
    result = semantic_deduplication(embeddings, threshold=0.99)
    assert result.keep_indices == [0, 2, 4]
    assert result.remove_indices == [1, 3]
    assert result.duplicate_of == {1: 0, 3: 2}
    assert result.groups == [[0, 1], [2, 3]]
    assert result.num_duplicates == 2
    assert result.num_kept == 3


def test_dedup_transitive_golden():
    from sentence_transformers.util import semantic_deduplication

    embeddings = [
        [1.0, 0.0],
        [0.9961946980917455, 0.08715574274765817],  # 5 degrees
        [0.984807753012208, 0.17364817766693033],  # 10 degrees
        [0.0, 1.0],
    ]
    result = semantic_deduplication(embeddings, threshold=0.99)
    assert result.groups == [[0, 1, 2]]
    assert result.keep_indices == [0, 3]
    assert result.duplicate_of == {1: 0, 2: 0}


def test_dedup_threshold_is_inclusive():
    from sentence_transformers.util import semantic_deduplication

    # cosine([1,0], [3,4]) == 3/5 == 0.6 exactly
    embeddings = [[1.0, 0.0], [3.0, 4.0]]
    assert semantic_deduplication(embeddings, threshold=0.6).keep_indices == [0]
    assert semantic_deduplication(embeddings, threshold=0.6000001).keep_indices == [0, 1]


def test_dedup_apply_and_validation():
    from sentence_transformers.util import semantic_deduplication

    sentences = ["the cat sits", "the cat sits", "a dog runs", "completely different"]
    embeddings = [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    result = semantic_deduplication(embeddings, threshold=0.99)
    assert result.apply(sentences) == ["the cat sits", "a dog runs", "completely different"]
    with pytest.raises(ValueError):
        semantic_deduplication(embeddings, threshold=1.5)
    with pytest.raises(ValueError):
        semantic_deduplication(embeddings, threshold=-1.5)


# ---------------------------------------------------------------------------
# Clustering metrics (frozen goldens)
# ---------------------------------------------------------------------------


def test_metric_goldens():
    from sentence_transformers.util import (
        adjusted_rand_score,
        cluster_accuracy_score,
        completeness_score,
        fowlkes_mallows_score,
        homogeneity_score,
        normalized_mutual_info_score,
        purity_score,
        v_measure_score,
    )

    assert v_measure_score(GOLD_LABELS, PRED_LABELS) == pytest.approx(GOLDEN_V_MEASURE, abs=TOL)
    assert homogeneity_score(GOLD_LABELS, PRED_LABELS) == pytest.approx(GOLDEN_HOMOGENEITY, abs=TOL)
    assert completeness_score(GOLD_LABELS, PRED_LABELS) == pytest.approx(GOLDEN_COMPLETENESS, abs=TOL)
    assert normalized_mutual_info_score(GOLD_LABELS, PRED_LABELS) == pytest.approx(GOLDEN_NMI, abs=TOL)
    assert adjusted_rand_score(GOLD_LABELS, PRED_LABELS) == pytest.approx(GOLDEN_ARI, abs=TOL)
    assert fowlkes_mallows_score(GOLD_LABELS, PRED_LABELS) == pytest.approx(GOLDEN_FM, abs=TOL)
    assert purity_score(GOLD_LABELS, PRED_LABELS) == pytest.approx(GOLDEN_PURITY, abs=TOL)
    assert cluster_accuracy_score(GOLD_LABELS, PRED_LABELS) == pytest.approx(GOLDEN_ACCURACY, abs=TOL)


def test_metric_perfect_partition():
    from sentence_transformers.util import adjusted_rand_score, v_measure_score

    assert v_measure_score([0, 0, 1, 1, 2, 2], [5, 5, 3, 3, 9, 9]) == pytest.approx(1.0)
    assert adjusted_rand_score([0, 0, 1, 1, 2, 2], [5, 5, 3, 3, 9, 9]) == pytest.approx(1.0)


def test_metric_validation():
    from sentence_transformers.util import cluster_accuracy_score, v_measure_score

    with pytest.raises(ValueError):
        v_measure_score([0, 1], [0])
    with pytest.raises(ValueError):
        cluster_accuracy_score([0, 1], [0])


# ---------------------------------------------------------------------------
# Representative selection
# ---------------------------------------------------------------------------


def test_select_representatives_golden():
    from sentence_transformers.util import select_representatives

    embeddings = [
        [0.0, 0.0],
        [1.0, 0.0],
        [0.5, 0.0],  # closest to centroid (0.5, 0)
        [10.0, 10.0],
        [11.0, 10.0],
        [10.5, 10.0],  # closest to centroid (10.5, 10)
    ]
    clustering = clustering_from_labels([0, 0, 0, 1, 1, 1])
    assert select_representatives(embeddings, clustering, top_k=1) == {0: [2], 1: [5]}
    with pytest.raises(ValueError):
        select_representatives(embeddings, clustering, top_k=0)


# ---------------------------------------------------------------------------
# ClusteringEvaluator
# ---------------------------------------------------------------------------


def test_evaluator_perfect_clustering(tmp_path):
    from sentence_transformers.evaluation import ClusteringEvaluator

    model = StubModel(CLEAN_EMBEDDINGS)
    evaluator = ClusteringEvaluator(sentences=SENTENCES, labels=LABELS, name="toy")
    metrics = evaluator(model, output_path=str(tmp_path), epoch=1, steps=100)

    assert evaluator.primary_metric == "toy_v_measure"
    assert metrics["toy_v_measure"] == pytest.approx(1.0, abs=TOL)
    assert metrics["toy_adjusted_rand"] == pytest.approx(1.0, abs=TOL)
    assert metrics["toy_normalized_mutual_info"] == pytest.approx(1.0, abs=TOL)
    assert metrics["toy_accuracy"] == pytest.approx(1.0, abs=TOL)

    # The evaluator must encode exactly the sentences it was given
    assert len(model.encode_calls) == 1
    assert model.encode_calls[0]["sentences"] == SENTENCES

    # CSV contract: family-standard file name, header and appended row
    csv_path = tmp_path / "clustering_evaluation_toy_results.csv"
    assert csv_path.exists()
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["epoch", "steps", "v_measure", "adjusted_rand", "normalized_mutual_info", "accuracy"]
    assert len(rows) == 2
    assert rows[1][0] == "1" and rows[1][1] == "100"
    assert float(rows[1][2]) == pytest.approx(1.0, abs=TOL)


def test_evaluator_mixed_clustering_goldens(tmp_path):
    from sentence_transformers.evaluation import ClusteringEvaluator

    model = StubModel(MIXED_EMBEDDINGS)
    evaluator = ClusteringEvaluator(sentences=SENTENCES, labels=LABELS)
    metrics = evaluator(model, output_path=str(tmp_path))
    assert metrics["v_measure"] == pytest.approx(GOLDEN_MIXED_V_MEASURE, abs=TOL)
    assert metrics["normalized_mutual_info"] == pytest.approx(GOLDEN_MIXED_NMI, abs=TOL)
    assert metrics["adjusted_rand"] == pytest.approx(GOLDEN_MIXED_ARI, abs=TOL)
    assert metrics["accuracy"] == pytest.approx(GOLDEN_MIXED_ACCURACY, abs=TOL)


def test_evaluator_csv_appends_and_write_csv_flag(tmp_path):
    from sentence_transformers.evaluation import ClusteringEvaluator

    model = StubModel(CLEAN_EMBEDDINGS)
    evaluator = ClusteringEvaluator(sentences=SENTENCES, labels=LABELS)
    evaluator(model, output_path=str(tmp_path), epoch=1, steps=10)
    evaluator(model, output_path=str(tmp_path), epoch=2, steps=20)
    with open(tmp_path / "clustering_evaluation_results.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert len(rows) == 3
    assert [row[0] for row in rows[1:]] == ["1", "2"]

    no_csv = ClusteringEvaluator(sentences=SENTENCES, labels=LABELS, write_csv=False, name="off")
    no_csv(model, output_path=str(tmp_path))
    assert not (tmp_path / "clustering_evaluation_off_results.csv").exists()


def test_evaluator_algorithms_and_validation():
    from sentence_transformers.evaluation import ClusteringEvaluator

    model = StubModel(CLEAN_EMBEDDINGS)
    agglom = ClusteringEvaluator(sentences=SENTENCES, labels=LABELS, algorithm="agglomerative")
    assert agglom(model)["v_measure"] == pytest.approx(1.0, abs=TOL)

    agglom_thr = ClusteringEvaluator(sentences=SENTENCES, labels=LABELS, algorithm="agglomerative", threshold=0.5)
    assert agglom_thr(model)["v_measure"] == pytest.approx(1.0, abs=TOL)

    with pytest.raises(ValueError):
        ClusteringEvaluator(sentences=["a", "b"], labels=["x"])
    with pytest.raises(ValueError):
        ClusteringEvaluator(sentences=["a"], labels=["x"], algorithm="dbscan")
    with pytest.raises(ValueError):
        ClusteringEvaluator(sentences=["a"], labels=["x"], algorithm="kmeans", threshold=0.5)

    # Default cluster count follows the number of distinct gold labels
    assert ClusteringEvaluator(sentences=SENTENCES, labels=LABELS).num_clusters == 2


# ---------------------------------------------------------------------------
# community_detection: existing behavior must be regressed AND fixed
# ---------------------------------------------------------------------------


# Three well-separated communities of the SAME size (3), so the deterministic
# tie-break (size-descending, then central-index-ascending) is what fully pins
# the output. The central index of each community is the smallest one.
CD_TIE_POINTS = [
    [1.0, 0.0], [0.99, 0.01], [0.98, 0.02],       # 0..2, +x
    [-1.0, 0.0], [-0.99, 0.01], [-0.98, 0.02],    # 3..5, -x
    [0.0, 1.0], [0.01, 0.99], [0.02, 0.98],       # 6..8, +y
]
CD_TIE_GOLDEN = [[0, 1, 2], [3, 4, 5], [6, 7, 8]]

# Three communities of DIFFERENT sizes (5, 4, 3); goldens pin the size-descending
# ordering. Central indices differ so the pure size sort is what matters.
CD_SIZE_POINTS = [
    [0.0, 1.0], [0.01, 0.99], [0.02, 0.98], [0.03, 0.97], [0.04, 0.96],  # 0..4 (size 5)
    [1.0, 0.0], [0.99, 0.01], [0.98, 0.02], [0.97, 0.03],                # 5..8 (size 4)
    [-1.0, 0.0], [-0.99, 0.01], [-0.98, 0.02],                           # 9..11 (size 3)
]
CD_SIZE_GOLDEN = [[0, 1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11]]


def test_community_detection_returns_list_of_lists_by_default():
    """The legacy return type is preserved: list[list[int]] of concrete Python ints."""
    from sentence_transformers.util import community_detection

    result = community_detection(CD_TIE_POINTS, threshold=0.9, min_community_size=3)
    assert isinstance(result, list)
    assert result == CD_TIE_GOLDEN
    for community in result:
        assert isinstance(community, list)
        for idx in community:
            assert isinstance(idx, int)


def test_community_detection_size_descending_tiebreak_pinned():
    """Communities are returned in descending-size order with a deterministic tie-break."""
    from sentence_transformers.util import community_detection

    result = community_detection(CD_SIZE_POINTS, threshold=0.9, min_community_size=3)
    assert result == CD_SIZE_GOLDEN


@pytest.mark.parametrize("batch_size", [1, 2, 3, 5, 7, 1024])
def test_community_detection_output_invariant_across_batch_size(batch_size):
    """Same embeddings, different batch_size → identical output. This is the core bug fix."""
    from sentence_transformers.util import community_detection

    result = community_detection(
        CD_TIE_POINTS, threshold=0.9, min_community_size=3, batch_size=batch_size
    )
    assert result == CD_TIE_GOLDEN


@pytest.mark.parametrize("batch_size", [1, 2, 3, 5, 12, 1024])
def test_community_detection_size_ordering_invariant_across_batch_size(batch_size):
    """The size-descending ordering must not silently swap on batch boundaries."""
    from sentence_transformers.util import community_detection

    result = community_detection(
        CD_SIZE_POINTS, threshold=0.9, min_community_size=3, batch_size=batch_size
    )
    assert result == CD_SIZE_GOLDEN


def test_community_detection_first_element_is_center():
    """Legacy contract: the first index in every community is the central point."""
    from sentence_transformers.util import community_detection

    result = community_detection(CD_TIE_POINTS, threshold=0.9, min_community_size=3)
    assert [community[0] for community in result] == [0, 3, 6]


def test_community_detection_zero_vector_excluded_and_no_nans():
    """All-zero embeddings must never join a community and must never crash the pipeline."""
    from sentence_transformers.util import community_detection

    embeddings = [
        [1.0, 0.0], [0.99, 0.01], [0.98, 0.02],       # 0..2
        [0.0, 0.0],                                    # 3 zero row
        [-1.0, 0.0], [-0.99, 0.01], [-0.98, 0.02],    # 4..6
        [0.0, 0.0],                                    # 7 zero row
    ]
    result = community_detection(embeddings, threshold=0.9, min_community_size=3)
    # Neither zero index appears anywhere, and communities are still fully found.
    assert result == [[0, 1, 2], [4, 5, 6]]
    for community in result:
        assert 3 not in community
        assert 7 not in community


def test_community_detection_return_result_object():
    """return_result=True produces a CommunityDetectionResult with the frozen contract."""
    from sentence_transformers.util import CommunityDetectionResult, community_detection

    embeddings = [
        [1.0, 0.0], [0.99, 0.01], [0.98, 0.02],
        [0.0, 0.0],
        [-1.0, 0.0], [-0.99, 0.01], [-0.98, 0.02],
    ]
    result = community_detection(embeddings, threshold=0.9, min_community_size=3, return_result=True)
    assert isinstance(result, CommunityDetectionResult)
    assert result.communities == [[0, 1, 2], [4, 5, 6]]
    assert result.central_indices == [0, 4]
    assert result.unclustered_indices == [3]
    assert result.num_communities == 2
    assert result.sizes == [3, 3]
    assert result.num_embeddings == 7
    # labels[i] is the community id or -1 for unclustered
    assert result.labels == [0, 0, 0, -1, 1, 1, 1]


def test_community_detection_result_promotes_to_clustering_result():
    """The result object can be converted to a ClusteringResult for downstream use."""
    from sentence_transformers.util import ClusteringResult, community_detection

    embeddings = [
        [1.0, 0.0], [0.99, 0.01], [0.98, 0.02],
        [0.0, 0.0],
        [-1.0, 0.0], [-0.99, 0.01], [-0.98, 0.02],
    ]
    result = community_detection(embeddings, threshold=0.9, min_community_size=3, return_result=True)
    clustering = result.as_clustering_result()
    assert isinstance(clustering, ClusteringResult)
    # Cluster ids are normalized by first-member: cluster containing 0 becomes 0.
    assert clustering.labels[0] == 0
    # Outlier index (3) ends up in its own cluster
    outlier_cluster = clustering.labels[3]
    assert outlier_cluster not in (clustering.labels[0], clustering.labels[4])
    assert clustering.num_clusters == 3


def test_community_detection_validation_threshold_and_min_size():
    """Invalid inputs raise ValueError."""
    from sentence_transformers.util import community_detection

    with pytest.raises(ValueError):
        community_detection(CD_TIE_POINTS, threshold=1.5)
    with pytest.raises(ValueError):
        community_detection(CD_TIE_POINTS, threshold=-1.5)
    with pytest.raises(ValueError):
        community_detection(CD_TIE_POINTS, threshold=0.9, min_community_size=0)
    with pytest.raises(ValueError):
        community_detection(CD_TIE_POINTS, threshold=0.9, min_community_size=-1)


def test_community_detection_empty_input():
    from sentence_transformers.util import CommunityDetectionResult, community_detection

    assert community_detection([], threshold=0.9, min_community_size=2) == []
    result = community_detection([], threshold=0.9, min_community_size=2, return_result=True)
    assert isinstance(result, CommunityDetectionResult)
    assert result.num_communities == 0
    assert result.unclustered_indices == []
    assert result.num_embeddings == 0


# ---------------------------------------------------------------------------
# Cross-integration: dedup must agree with community_detection on shape
# ---------------------------------------------------------------------------


def test_dedup_transitive_chain_covers_endpoints():
    """
    Chain (each consecutive pair 5° apart) where non-adjacent pairs are below threshold: the
    union-find behavior of semantic_deduplication must still fold the whole chain into one group.
    """
    from sentence_transformers.util import semantic_deduplication

    chain = [
        [1.0, 0.0],
        [0.9961946980917455, 0.08715574274765817],   # 5 deg
        [0.984807753012208, 0.17364817766693033],    # 10 deg
        [0.9659258262890683, 0.25881904510252074],   # 15 deg
        [0.9396926207859084, 0.3420201433256687],    # 20 deg
    ]
    dedup = semantic_deduplication(chain, threshold=0.99)
    assert dedup.groups == [[0, 1, 2, 3, 4]]
    assert dedup.keep_indices == [0]
    assert dedup.duplicate_of == {1: 0, 2: 0, 3: 0, 4: 0}


# ---------------------------------------------------------------------------
# Additional dedup determinism / scaling
# ---------------------------------------------------------------------------


def test_dedup_is_scale_invariant():
    """Scaling vectors by positive factors must not change duplicate groups."""
    from sentence_transformers.util import semantic_deduplication

    original = [[1.0, 0.0], [2.0, 0.0], [0.5, 0.0], [0.0, 1.0], [0.0, 3.0]]
    scaled = [[3.0, 0.0], [7.5, 0.0], [0.01, 0.0], [0.0, 2.5], [0.0, 0.4]]
    a = semantic_deduplication(original, threshold=0.99)
    b = semantic_deduplication(scaled, threshold=0.99)
    assert a.keep_indices == b.keep_indices == [0, 3]
    assert a.groups == b.groups == [[0, 1, 2], [3, 4]]


def test_dedup_representative_is_lowest_index():
    """Even when a later duplicate group member has a shorter/longer vector, the lowest index wins."""
    from sentence_transformers.util import semantic_deduplication

    embeddings = [
        [0.1, 0.0],   # low-magnitude, but index 0 -> must be kept
        [10.0, 0.0],
        [100.0, 0.0],
    ]
    result = semantic_deduplication(embeddings, threshold=0.99)
    assert result.keep_indices == [0]
    assert result.duplicate_of == {1: 0, 2: 0}


# ---------------------------------------------------------------------------
# kmeans hardened boundary tests
# ---------------------------------------------------------------------------


def test_kmeans_thread_count_invariant():
    """The result must not depend on the number of CPU threads torch uses internally."""
    import torch

    from sentence_transformers.util import kmeans_clustering

    original_threads = torch.get_num_threads()
    try:
        torch.set_num_threads(1)
        one = kmeans_clustering(BLOB_POINTS, num_clusters=3, seed=22).labels
        torch.set_num_threads(4)
        four = kmeans_clustering(BLOB_POINTS, num_clusters=3, seed=22).labels
    finally:
        torch.set_num_threads(original_threads)
    assert one == four == BLOB_GOLDEN_LABELS


def test_kmeans_all_duplicate_input():
    """When every embedding is the same point, k=1 must give one cluster and k=n must give singletons."""
    from sentence_transformers.util import kmeans_clustering

    embeddings = [[1.0, 1.0]] * 6
    assert kmeans_clustering(embeddings, num_clusters=1).labels == [0] * 6
    assert kmeans_clustering(embeddings, num_clusters=6).labels == list(range(6))


def test_kmeans_num_clusters_type_check():
    """Non-int num_clusters (e.g. float, bool) must raise ValueError, not silently coerce."""
    from sentence_transformers.util import kmeans_clustering

    with pytest.raises(ValueError):
        kmeans_clustering(BLOB_POINTS, num_clusters=3.0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        kmeans_clustering(BLOB_POINTS, num_clusters=True)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Agglomerative hardened tests
# ---------------------------------------------------------------------------


def test_agglomerative_cosine_threshold_semantics():
    """threshold in cosine metric is (1 - cosine_similarity); pins the merge behavior."""
    from sentence_transformers.util import agglomerative_clustering

    # Two blobs of size 3, with cosine sim 1.0 within each and sim 0.0 between.
    embeddings = [
        [1.0, 0.0], [0.99, 0.01], [0.98, 0.02],
        [0.0, 1.0], [0.01, 0.99], [0.02, 0.98],
    ]
    # threshold=0.5 (i.e. only merge when cosine_sim >= 0.5): the two blobs stay separate.
    result = agglomerative_clustering(embeddings, threshold=0.5, metric="cosine")
    assert as_partition(result) == {frozenset({0, 1, 2}), frozenset({3, 4, 5})}
    # threshold=1.5 (>1): every pair is under threshold → all one cluster.
    result = agglomerative_clustering(embeddings, threshold=1.5, metric="cosine")
    assert result.num_clusters == 1


def test_agglomerative_scale_invariant_cosine():
    """Cosine agglomerative must be scale invariant."""
    from sentence_transformers.util import agglomerative_clustering

    original = [
        [1.0, 0.0], [0.99, 0.01],
        [-1.0, 0.0], [-0.99, 0.01],
    ]
    scaled = [
        [10.0, 0.0], [0.5, 0.005],
        [-3.0, 0.0], [-0.1, 0.001],
    ]
    a = agglomerative_clustering(original, num_clusters=2, metric="cosine")
    b = agglomerative_clustering(scaled, num_clusters=2, metric="cosine")
    assert a.partition_equals(b)


# ---------------------------------------------------------------------------
# Extra metric contract
# ---------------------------------------------------------------------------


def test_metric_label_type_agnostic():
    """v_measure_score must accept str labels and give the same result as int labels."""
    from sentence_transformers.util import v_measure_score

    int_v = v_measure_score(GOLD_LABELS, PRED_LABELS)
    str_v = v_measure_score(
        [f"g{x}" for x in GOLD_LABELS], [f"p{x}" for x in PRED_LABELS]
    )
    assert int_v == pytest.approx(str_v, abs=TOL)


def test_metric_cluster_id_permutation_invariance():
    """Permuting predicted-cluster ids must not change any metric value."""
    from sentence_transformers.util import (
        adjusted_rand_score,
        fowlkes_mallows_score,
        normalized_mutual_info_score,
        v_measure_score,
    )

    # Permute 0<->1<->2<->3
    perm = {0: 3, 1: 0, 2: 1, 3: 2}
    permuted = [perm[p] for p in PRED_LABELS]
    for fn in (v_measure_score, normalized_mutual_info_score, adjusted_rand_score, fowlkes_mallows_score):
        assert fn(GOLD_LABELS, PRED_LABELS) == pytest.approx(fn(GOLD_LABELS, permuted), abs=TOL)


def test_v_measure_beta_zero_reduces_to_homogeneity():
    """v_measure with beta=0 reduces to pure homogeneity (matches sklearn's convention)."""
    from sentence_transformers.util import homogeneity_score, v_measure_score

    assert v_measure_score(GOLD_LABELS, PRED_LABELS, beta=0.0) == pytest.approx(
        homogeneity_score(GOLD_LABELS, PRED_LABELS), abs=TOL
    )


def test_v_measure_beta_negative_raises():
    from sentence_transformers.util import v_measure_score

    with pytest.raises(ValueError):
        v_measure_score(GOLD_LABELS, PRED_LABELS, beta=-0.5)


# ---------------------------------------------------------------------------
# ClusteringResult conventions: partition_equals, cluster id numbering
# ---------------------------------------------------------------------------


def test_partition_equals_ignores_cluster_ids():
    """Two ClusteringResult objects with the same partition but different ids must equal."""
    a = clustering_from_labels([0, 0, 1, 1, 2, 2])
    b = clustering_from_labels([3, 3, 5, 5, 1, 1])
    assert a.partition_equals(b)
    c = clustering_from_labels([0, 1, 0, 1, 2, 2])
    assert not a.partition_equals(c)


def test_clustering_result_first_member_normalization_regressed():
    """cluster containing index 0 must be cluster 0, next-smallest first member → cluster 1, etc."""
    from sentence_transformers.util import kmeans_clustering

    # Feed points where the raw kmeans internally could label them in any order; after normalization
    # the cluster with index 0 must be cluster 0.
    embeddings = [
        [-1.0, 0.0], [-0.99, 0.01],   # 0..1
        [0.0, 1.0], [0.01, 0.99],     # 2..3
        [1.0, 0.0], [0.99, 0.01],     # 4..5
    ]
    result = kmeans_clustering(embeddings, num_clusters=3, seed=22)
    assert result.labels[0] == 0
    assert sorted(set(result.labels)) == [0, 1, 2]
    # First 2 in same cluster, next 2 in same cluster, last 2 in same cluster
    assert result.labels[0] == result.labels[1]
    assert result.labels[2] == result.labels[3]
    assert result.labels[4] == result.labels[5]


# ---------------------------------------------------------------------------
# select_representatives: sanity beyond the one-cluster golden
# ---------------------------------------------------------------------------


def test_select_representatives_more_than_size_returns_whole_cluster():
    """top_k > cluster_size returns the whole cluster (ordered by distance)."""
    from sentence_transformers.util import select_representatives

    embeddings = [[0.0, 0.0], [1.0, 0.0]]
    clustering = clustering_from_labels([0, 0])
    reps = select_representatives(embeddings, clustering, top_k=5)
    assert sorted(reps[0]) == [0, 1]


def test_select_representatives_length_mismatch_raises():
    from sentence_transformers.util import select_representatives

    with pytest.raises(ValueError):
        select_representatives([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]], clustering_from_labels([0, 0]))


# ---------------------------------------------------------------------------
# Evaluator: reachable via deprecated path AND behaves the same
# ---------------------------------------------------------------------------


def test_evaluator_reachable_via_deprecated_path(tmp_path):
    """The `sentence_transformers.evaluation` package (deprecated path) still resolves."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from sentence_transformers.evaluation import ClusteringEvaluator as OldPath

    model = StubModel(CLEAN_EMBEDDINGS)
    evaluator = OldPath(sentences=SENTENCES, labels=LABELS)
    metrics = evaluator(model, output_path=str(tmp_path))
    assert metrics["v_measure"] == pytest.approx(1.0, abs=TOL)


def test_evaluator_with_real_tiny_model(tmp_path):
    """End-to-end with a real, tiny, randomly-initialized SentenceTransformer."""
    pytest.importorskip("transformers")
    import torch
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import BertConfig, BertModel, BertTokenizerFast

    from sentence_transformers import SentenceTransformer
    from sentence_transformers.evaluation import ClusteringEvaluator

    torch.manual_seed(0)
    vocab = {
        "[PAD]": 0,
        "[UNK]": 1,
        "[CLS]": 2,
        "[SEP]": 3,
        "[MASK]": 4,
        "apple": 5,
        "banana": 6,
        "orange": 7,
        "dog": 8,
        "cat": 9,
        "bird": 10,
    }
    backend = Tokenizer(models.WordLevel(vocab=vocab, unk_token="[UNK]"))
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer = BertTokenizerFast(
        tokenizer_object=backend,
        unk_token="[UNK]",
        sep_token="[SEP]",
        pad_token="[PAD]",
        cls_token="[CLS]",
        mask_token="[MASK]",
    )
    config = BertConfig(
        vocab_size=len(vocab),
        hidden_size=32,
        num_hidden_layers=2,
        num_attention_heads=2,
        intermediate_size=64,
        max_position_embeddings=32,
    )
    model_dir = tmp_path / "tiny-model"
    BertModel(config).save_pretrained(model_dir)
    tokenizer.save_pretrained(model_dir)

    model = SentenceTransformer(str(model_dir), device="cpu")
    evaluator = ClusteringEvaluator(
        sentences=["apple banana", "apple orange", "dog cat", "dog bird"],
        labels=["fruit", "fruit", "animal", "animal"],
        name="tiny",
    )
    metrics = evaluator(model, output_path=str(tmp_path / "out"))
    assert evaluator.primary_metric == "tiny_v_measure"
    assert set(metrics) == {
        "tiny_v_measure",
        "tiny_adjusted_rand",
        "tiny_normalized_mutual_info",
        "tiny_accuracy",
    }
    for key, value in metrics.items():
        assert math.isfinite(value), (key, value)
        assert -1.0 - TOL <= value <= 1.0 + TOL, (key, value)
