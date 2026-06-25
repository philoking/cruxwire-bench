"""FastAPI + HTMX console.

Phase 1 surface (spec P0-2..P0-4): pick a day, size a span from one 2-hour block
up to the full day, and view the full clustering — every cluster and every
singleton together — re-clustering as the span changes. Marking and re-run
scoring (Phase 2) are stubbed with clear extension points.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from schema import SCHEMA_VERSION
from .. import config, engine
from ..corpus import Corpus

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))

app = FastAPI(title="Cruxwire Clustering Bench")


def get_corpus() -> Corpus:
    # Cheap to construct (in-memory DuckDB); one per request keeps it simple and
    # avoids cross-request connection sharing. Optimize later if needed.
    return Corpus()


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    corpus = get_corpus()
    days = corpus.days()
    return templates.TemplateResponse(
        request,
        "day.html",
        {
            "days": days,
            "schema_version": SCHEMA_VERSION,
            "warning": corpus.schema_warning,
            "corpus_dir": str(config.CORPUS_DIR),
        },
    )


@app.get("/day/{day}", response_class=HTMLResponse)
def day_blocks(request: Request, day: str):
    corpus = get_corpus()
    blocks = corpus.blocks(day)
    return templates.TemplateResponse(
        request,
        "blocks.html",
        {"day": day, "blocks": blocks, "total": sum(b.story_count for b in blocks)},
    )


@app.get("/window", response_class=HTMLResponse)
def window(request: Request, day: str, start: str, end: str, threshold: float = engine.DEFAULT_SIM_THRESHOLD):
    """The primary surface: full clustering for the current span."""
    corpus = get_corpus()
    articles = corpus.load_span(day, start, end)
    result = engine.cluster(articles, sim_threshold=threshold)

    # Baseline fidelity: does our re-cluster reproduce the recorded prod_cluster_id?
    divergence = _baseline_divergence(articles, result)

    clusters = [
        {
            "cluster_id": result.cluster_id_of[grp[0]],
            "members": [articles[i] for i in grp],
            "size": len(grp),
        }
        for grp in result.clusters
        if len(grp) > 1
    ]
    singletons = [articles[i] for i in result.singletons]

    return templates.TemplateResponse(
        request,
        "window.html",
        {
            "day": day,
            "start": start,
            "end": end,
            "threshold": threshold,
            "clusters": clusters,
            "singletons": singletons,
            "total": len(articles),
            "n_clusters": len(clusters),
            "divergence": divergence,
        },
    )


def _baseline_divergence(articles: list[dict], result: engine.ClusterResult) -> dict | None:
    """Compare our cluster assignment to the recorded prod_cluster_id.

    Returns None when no prod labels are present (e.g. synthetic corpus), else a
    summary so the operator knows whether to trust comparisons against baseline
    (spec → Replay Fidelity). This is at the threshold the operator is viewing —
    a true baseline check should use production threshold; see SPEC_REVIEW.md.
    """
    labeled = [(i, a) for i, a in enumerate(articles) if a.get("prod_cluster_id")]
    if not labeled:
        return None
    # Compare co-membership: for each prod cluster, did our run keep it together?
    mismatched = 0
    for _i, a in labeled:
        # crude per-article check: same prod members should share our cluster id.
        same_prod = [j for j, b in labeled if b["prod_cluster_id"] == a["prod_cluster_id"]]
        ours_ids = {result.cluster_id_of.get(j) for j in same_prod}
        if len(ours_ids) > 1:
            mismatched += 1
    return {"labeled": len(labeled), "mismatched": mismatched}
