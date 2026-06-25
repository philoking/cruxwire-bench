"""Engine tests: the bench must reproduce cruxwire's anchored clustering exactly.

The reference implementation here is a direct transcription of cruxwire's
pipeline.py `cluster()` (anchored greedy, score-descending order, no embedding =>
singleton). We assert the vectorized engine agrees with it on random inputs.
"""

from __future__ import annotations

import math
import random

from bench import engine


def _ref_cluster(articles, sim_threshold=0.82):
    """Plain transcription of cruxwire's cluster() membership logic."""
    def norm(vec):
        n = math.sqrt(sum(x * x for x in vec))
        return [x / n for x in vec] if n else None

    norms = [norm(a["embedding"]) if a.get("embedding") else None for a in articles]
    n = len(articles)

    def cos(u, v):
        return sum(x * y for x, y in zip(u, v))

    order = sorted((i for i in range(n) if norms[i] is not None),
                   key=lambda i: articles[i]["score"], reverse=True)
    anchors, members = [], {}
    for i in order:
        best, best_sim = None, sim_threshold
        for a in anchors:
            s = cos(norms[i], norms[a])
            if s > best_sim:
                best, best_sim = a, s
        if best is None:
            anchors.append(i)
            members[i] = [i]
        else:
            members[best].append(i)
    groups = list(members.values())
    groups.extend([i] for i in range(n) if norms[i] is None)
    # canonical: frozenset of ids per group
    return {frozenset(articles[i]["id"] for i in g) for g in groups}


def _engine_groups(articles, threshold=0.82):
    res = engine.cluster(articles, sim_threshold=threshold)
    return {frozenset(articles[i]["id"] for i in g) for g in res.clusters}


def _rand_articles(seed, n=40, dim=16):
    rng = random.Random(seed)
    centroids = [[rng.gauss(0, 1) for _ in range(dim)] for _ in range(5)]
    arts = []
    for k in range(n):
        if rng.random() < 0.2:
            emb = None
        else:
            c = centroids[rng.randrange(len(centroids))]
            emb = [c[j] + rng.gauss(0, 0.3) for j in range(dim)]
        arts.append({"id": f"a{k}", "score": rng.uniform(0, 10),
                     "has_image": rng.random() < 0.5, "embedding": emb})
    return arts


def test_matches_reference_across_seeds_and_thresholds():
    for seed in range(25):
        arts = _rand_articles(seed)
        for thr in (0.5, 0.7, 0.82, 0.95):
            assert _engine_groups(arts, thr) == _ref_cluster(arts, thr), (seed, thr)


def test_unembedded_are_singletons():
    arts = [
        {"id": "x", "score": 5, "embedding": None},
        {"id": "y", "score": 5, "embedding": None},
    ]
    res = engine.cluster(arts)
    assert sorted(res.singletons) == [0, 1]
    assert res.n_clusters == 0


def test_representative_is_highest_score():
    # Two near-identical vectors; higher score must be the rep (group[0]).
    arts = [
        {"id": "low", "score": 1.0, "embedding": [1.0, 0.0, 0.0]},
        {"id": "high", "score": 9.0, "embedding": [0.99, 0.01, 0.0]},
    ]
    res = engine.cluster(arts, sim_threshold=0.5)
    assert len(res.clusters) == 1
    rep_idx = res.clusters[0][0]
    assert arts[rep_idx]["id"] == "high"
    assert res.cluster_id_of[0] == "high"


def test_empty_span():
    res = engine.cluster([])
    assert res.clusters == [] and res.singletons == []
