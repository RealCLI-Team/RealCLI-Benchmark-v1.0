# sentence-transformers is missing a semantic clustering/dedup toolchain, plus several gripes about existing behavior

`/app` is the development source of sentence-transformers (installed with `pip install -e .`, CPU, offline environment; torch / transformers / scikit-learn / scipy / pytest / tokenizers are all installed). The team has recently been working on downstream semantic search tasks and reported a pile of pain points they'd like cleaned up in one go:

1. **Want to cluster by a "preset number of clusters K", but there's no tool for it.** The library only has the threshold-based graph-style clustering `community_detection`, which can only cluster by a "similarity threshold" and can't handle a basic ask like "I want K clusters". Classic k-means and hierarchical (agglomerative) clustering are both absent too.

2. **Want to do semantic deduplication, and there's no tool for that either.** To group mutually similar items in the embeddings and keep only one per group, users have to hand-roll a similarity matrix plus union-find. And the requirements almost always include "transitive closure" — if A resembles B and B resembles C, then even if A and C don't pass the threshold, they should still be treated as one group — which is easy to get wrong when writing it yourself.

3. **Want to pull "the few most typical sentences in each cluster" for human review, but there's no ready-made interface.**

4. **When training a model, want to quantify "can this model's embeddings cluster same-class corpora together", but can't find an Evaluator that takes `(sentences, gold_labels)` and computes v_measure / ARI / NMI / accuracy all at once.** Right now the only option is to take the `model.encode()` output and run it through sklearn yourself — everyone writes it differently, and it can't plug into the SentenceTransformer training loop as an evaluator with CSV logging, `primary_metric`, and so on.

5. **The existing `community_detection` itself has some gripes worth fixing along the way:**
   - **Sensitive to an irrelevant parameter — output is unstable.** With the exact same embeddings, merely changing `batch_size` (e.g. from 1024 to 3) makes the order of the returned communities jump around. Users can't pin the results down for regression testing. The cause: when multiple communities have the same size, their order is "naturally" preserved from the batch processing order, with no stable tie-break.
   - **Zero vectors pollute the results.** If the input contains an all-zero embedding, it goes through the internal normalization pipeline and ends up with dot products identically 0 against every other vector; once the threshold is negative or the code path changes, it shows up in communities as a ghost member, or eats up topk slots. What users want: **zero vectors never participate in any community** — neither as a center nor as a member.
   - The old calling convention must stay compatible: the default return value is still `list[list[int]]`, the public signature is unchanged, and the first element of each community is still its central point — only now the same input is fully reproducible across batch_size / platforms. We also want an explicit result-object exit — switching the return value to a structured object via a keyword-only argument — so users can read fields like `.labels`, `.central_indices`, `.unclustered_indices`, `.sizes` directly without manually slicing the list-of-lists again.

Please fill in the entire semantic clustering / deduplication / representative selection / cluster quality metrics / clustering evaluator workflow, in the style of the existing modules under `sentence_transformers/util/` (retrieval, quantization, similarity, etc.) and `sentence_transformers/sentence_transformer/evaluation/` (EmbeddingSimilarityEvaluator, TripletEvaluator, etc.). The following constraints must be satisfied:

- All changes happen inside `/app`; the environment is **completely offline** — do not touch the network. torch / transformers / scikit-learn / scipy / pytest / tokenizers are already installed. **Do not download any pretrained model weights** — if you genuinely need a model for verification, construct a small randomly-initialized configuration locally on the fly.
- The public APIs must be importable from `sentence_transformers.util`; the evaluator must be available from both `sentence_transformers.evaluation` (the old path, via the compatibility mapping) and `sentence_transformers.sentence_transformer.evaluation` (the new path). Naming conventions must stay consistent with the existing families in the repository.
- Output must be **deterministic**: kmeans has a `seed` parameter and is fully reproducible for the same parameters and inputs (independent of CPU thread count); hierarchical clustering merges the pair with the smallest indices first when multiple cluster pairs have equal distances; changing `batch_size` in `community_detection` must not change the results; the ordering of `select_representatives` and `semantic_deduplication` is fully reproducible.
- Normalization of cluster ids in the clustering result object: the cluster containing input sample 0 is always id 0, and the rest are numbered in order of each cluster's smallest member index — this way two correct implementations can agree on "same structure".
- Similarity is a comparison of "direction": any positive rescaling of the embeddings must not change the dedup result; collinear vectors of different magnitudes should be treated as the same direction.
- Documentation follows along (add entries under `docs/package_reference/`, and a usage section in `docs/sentence_transformer/usage/usage.rst`), matching the style of the existing sections. Also fill in same-style coverage in the relevant sections of the repository's existing test subdirectories.

### Required API surface

These exact names must be importable from `sentence_transformers.util`:

- `kmeans_clustering(embeddings, num_clusters=, seed=)`
- `agglomerative_clustering(embeddings, num_clusters= / threshold=, linkage=, metric=)` with `linkage ∈ {"single", "complete", "average"}` and `metric ∈ {"cosine", "euclidean"}`
- `semantic_deduplication(embeddings, threshold=)`
- `select_representatives(embeddings, clustering, top_k=, beta=)`
- `ClusteringResult` with fields `clusters` (a dict of cluster id → member indices), `cluster_sizes`, and methods `largest_clusters(n)`, `smallest_clusters(n)`, `partition_equals(other)`
- `DeduplicationResult` with fields `keep_indices`, `remove_indices`, `duplicate_of`, `groups`, `num_duplicates`, `num_kept`, and method `apply(items)`
- `CommunityDetectionResult` with fields `communities`, `num_communities`, `num_embeddings`, and method `as_clustering_result()`; the keyword-only parameter of `community_detection` that opts into this result object is named `return_result`
- the 8 metric functions: `v_measure_score`, `homogeneity_score`, `completeness_score`, `normalized_mutual_info_score`, `adjusted_rand_score`, `fowlkes_mallows_score`, `purity_score`, `cluster_accuracy_score`

The evaluator is `ClusteringEvaluator` (reachable from both evaluation paths). Its `primary_metric` is `"<name>_v_measure"`; it logs to a CSV file named `clustering_evaluation[_<name>]_results.csv` (the `_<name>` infix is present only when a `name` is given) with header `[epoch, steps, v_measure, adjusted_rand, normalized_mutual_info, accuracy]`; repeated calls append one row per call to the same file; with `write_csv=False` no file is written.

### Semantics that must hold exactly

1. `community_detection`: communities are ordered by size descending, ties broken by central index ascending; within a community the central point comes first and the remaining members follow in descending similarity to the center.
2. `semantic_deduplication`: the threshold is a closed interval — cosine similarity exactly equal to `threshold` already counts as a duplicate; the kept representative of each group is its lowest index, and `duplicate_of` maps every removed index to that representative.
3. `agglomerative_clustering` with `metric="cosine"`: `threshold` is a cosine **distance** (`1 − cosine_similarity`), so values greater than 1 are meaningful.
4. `cluster_accuracy_score`: the accuracy after the optimal (Hungarian) matching between clusters and gold labels.
5. `ClusteringEvaluator`: the default `algorithm` is `"kmeans"` and the default `num_clusters` is the number of distinct gold labels; `algorithm="agglomerative"` (optionally with `threshold=`) is supported; combining `algorithm="kmeans"` with a `threshold` raises; the model encodes the sentences exactly once per evaluation call.
6. Invalid parameters always raise `ValueError` — including a non-`int` `num_clusters` (e.g. a float or a bool) and a negative `beta`.
