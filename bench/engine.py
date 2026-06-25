"""The bench clustering engine — exact, CPU-only, vectorized.

This faithfully reproduces cruxwire's production `cluster()` (pipeline.py) so a
baseline re-run reproduces the recorded `prod_cluster_id`, then exposes the same
machinery for parameter sweeps and alternative embedding models.

How cruxwire clusters (read from philoking/cruxwire pipeline.py `cluster()`):

    * Each story is *anchored* on its highest-scoring article. Articles are
      processed in score-descending order. An article joins the nearest existing
      anchor whose cosine similarity exceeds `sim_threshold`; otherwise it starts
      a new anchor. This is anchored greedy assignment, NOT single-link union-find
      — deliberately, to avoid transitive chaining (A~B, B~C ⇒ A~C).
    * Articles without an embedding are always singletons.
    * A cluster's representative is its highest-scoring member (tie-break:
      has-image). cluster_id == the representative article's id.

The spec's "Window Clustering Is Exact" note: we L2-normalize the span's
embeddings into a matrix X and compute the full cosine matrix as X @ X.T (BLAS,
~1s for a few thousand stories), then run the anchored assignment over matrix
lookups instead of a Python pairwise loop. Same result, fast.

NOTE on replay fidelity: the spec's Replay Fidelity section says to replay in
`ingested_at` order. cruxwire actually orders by *score* descending. We
reproduce the real algorithm (score order); see SPEC_REVIEW.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# cruxwire defaults (pipeline.py): SIM_THRESHOLD=0.82, BOOST_CAP / BOOST_K feed
# the rank boost, not membership; we keep boost out of membership decisions.
DEFAULT_SIM_THRESHOLD = 0.82


@dataclass
class ClusterResult:
    """Outcome of clustering a span.

    clusters: list of clusters, each a list of article indices (into `articles`),
              with the representative index first.
    singletons: indices that clustered with nothing (their own one-member group).
    cluster_id_of: index -> cluster_id (the rep article's id), matching cruxwire.
    """

    clusters: list[list[int]] = field(default_factory=list)
    singletons: list[int] = field(default_factory=list)
    cluster_id_of: dict[int, str] = field(default_factory=dict)

    @property
    def n_clusters(self) -> int:
        return len([c for c in self.clusters if len(c) > 1])

    def groups(self) -> list[list[int]]:
        """All groups (multi-member clusters + singletons), each as index lists."""
        return self.clusters


def l2_normalize(mat: np.ndarray) -> np.ndarray:
    """Row-wise L2 normalize; zero rows are left as zeros (cosine 0 to everything)."""
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


def cluster(
    articles: list[dict],
    sim_threshold: float = DEFAULT_SIM_THRESHOLD,
    embedding_key: str = "embedding",
) -> ClusterResult:
    """Cluster a frozen span of articles, reproducing cruxwire's anchored algorithm.

    Each article dict needs at least: ``id``, ``score`` (float; default 0.0),
    ``embedding`` (list[float] or None), and optionally ``has_image``/``image``.

    `embedding_key` lets a re-run cluster on an alternative model's stored vectors
    (the embedding-model-as-parameter capability) — pass e.g. "embedding_alt".
    """
    n = len(articles)
    result = ClusterResult()
    if n == 0:
        return result

    def score_of(i: int) -> float:
        try:
            return float(articles[i].get("score") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def has_image(i: int) -> int:
        a = articles[i]
        return 1 if (a.get("has_image") or a.get("image")) else 0

    embedded = [i for i in range(n) if articles[i].get(embedding_key)]
    bare = [i for i in range(n) if not articles[i].get(embedding_key)]

    members: dict[int, list[int]] = {}
    if embedded:
        # Build the normalized matrix in `embedded` order, then the full cosine
        # matrix. local index (0..m-1) ↔ global article index via `embedded`.
        X = np.asarray([articles[i][embedding_key] for i in embedded], dtype=np.float64)
        Xn = l2_normalize(X)
        sim = Xn @ Xn.T  # exact pairwise cosine; the spec's XX^T

        local_of = {g: loc for loc, g in enumerate(embedded)}
        # Process in score-descending order (cruxwire's anchoring order).
        order = sorted(embedded, key=score_of, reverse=True)
        anchors: list[int] = []  # global indices of anchors, creation order
        for i in order:
            li = local_of[i]
            best_anchor, best_sim = None, sim_threshold
            for a in anchors:
                s = float(sim[li, local_of[a]])
                if s > best_sim:
                    best_anchor, best_sim = a, s
            if best_anchor is None:
                anchors.append(i)
                members[i] = [i]
            else:
                members[best_anchor].append(i)

    groups = list(members.values())
    groups.extend([i] for i in bare)  # un-embedded articles are singletons

    for grp in groups:
        rep = max(grp, key=lambda i: (score_of(i), has_image(i)))
        cid = str(articles[rep]["id"])
        # Representative first within the group.
        ordered = [rep] + [i for i in grp if i != rep]
        result.clusters.append(ordered)
        for i in grp:
            result.cluster_id_of[i] = cid
        if len(grp) == 1:
            result.singletons.append(grp[0])

    # Stable, readable ordering: larger clusters first, then by rep score.
    result.clusters.sort(key=lambda g: (-len(g), -score_of(g[0])))
    return result


def cosine_matrix(articles: list[dict], embedding_key: str = "embedding") -> tuple[np.ndarray, list[int]]:
    """Full pairwise cosine matrix over embedded articles (exact). Returns
    (matrix, embedded_indices) so the UI can show the true cosine between any
    two stories — a miss is never hidden behind "never retrieved as a candidate".
    """
    embedded = [i for i, a in enumerate(articles) if a.get(embedding_key)]
    if not embedded:
        return np.zeros((0, 0)), []
    X = np.asarray([articles[i][embedding_key] for i in embedded], dtype=np.float64)
    Xn = l2_normalize(X)
    return Xn @ Xn.T, embedded
