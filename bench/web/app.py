"""FastAPI + HTMX console.

Phase 1 (P0-2..P0-4): pick a day, size a span, view the full clustering.
Phase 2 (P0-5..P0-7): mark mistakes in place, re-run at new params, and score
the result against the marks with a span-scoped diff vs the production baseline.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from schema import SCHEMA_VERSION
from .. import config, engine, marks, scoring
from ..corpus import Corpus

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
app = FastAPI(title="Cruxwire Clustering Bench")

# The faithful baseline clusters at production params (spec → Replay Fidelity).
BASELINE_THRESHOLD = engine.DEFAULT_SIM_THRESHOLD


def get_corpus() -> Corpus:
    return Corpus()


def _id_map(articles: list[dict], result: engine.ClusterResult) -> dict[str, str]:
    """index->cluster_id  ⇒  article_id->cluster_id (what scoring/marks speak)."""
    return {articles[i]["id"]: cid for i, cid in result.cluster_id_of.items()}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    corpus = get_corpus()
    days = corpus.days()
    return templates.TemplateResponse(
        request, "day.html",
        {"days": days, "schema_version": SCHEMA_VERSION,
         "warning": corpus.schema_warning, "corpus_dir": str(config.CORPUS_DIR)},
    )


@app.get("/day/{day}", response_class=HTMLResponse)
def day_blocks(request: Request, day: str):
    corpus = get_corpus()
    blocks = corpus.blocks(day)
    return templates.TemplateResponse(
        request, "blocks.html",
        {"day": day, "blocks": blocks, "total": sum(b.story_count for b in blocks)},
    )


def _render_window(request: Request, day: str, start: str, end: str, threshold: float):
    """Cluster the span at `threshold`, score it against the day's marks, and
    diff it against the production baseline. Shared by /window and mark actions."""
    corpus = get_corpus()
    articles = corpus.load_span(day, start, end)

    current = engine.cluster(articles, sim_threshold=threshold)
    cur_map = _id_map(articles, current)

    day_marks = marks.list_marks(day)
    score = scoring.score_marks(day_marks, cur_map)
    unsatisfied = set(score.unsatisfied)
    marked_ids = {i for m in day_marks for i in m.article_ids}

    # Diff vs baseline (only meaningful when the operator has moved off prod params).
    diff = None
    collateral = 0
    if abs(threshold - BASELINE_THRESHOLD) > 1e-9:
        baseline = engine.cluster(articles, sim_threshold=BASELINE_THRESHOLD)
        diff = scoring.diff_clusterings(_id_map(articles, baseline), cur_map)
        collateral = scoring.collateral(diff, day_marks)

    clusters = [
        {"cluster_id": cur_map[articles[grp[0]]["id"]],
         "members": [articles[i] for i in grp], "size": len(grp),
         "member_ids": [articles[i]["id"] for i in grp]}
        for grp in current.clusters if len(grp) > 1
    ]
    singletons = [articles[i] for i in current.singletons]

    return templates.TemplateResponse(
        request, "window.html",
        {"day": day, "start": start, "end": end, "threshold": threshold,
         "baseline_threshold": BASELINE_THRESHOLD,
         "clusters": clusters, "singletons": singletons,
         "total": len(articles), "n_clusters": len(clusters),
         "marks": day_marks, "score": score, "diff": diff, "collateral": collateral,
         "unsatisfied": unsatisfied, "marked_ids": marked_ids},
    )


@app.get("/window", response_class=HTMLResponse)
def window(request: Request, day: str, start: str, end: str,
           threshold: float = BASELINE_THRESHOLD):
    return _render_window(request, day, start, end, threshold)


def _collect_ids(ids: list[str], ids_csv: str) -> list[str]:
    out = list(ids or [])
    if ids_csv:
        out += [x for x in ids_csv.split(",") if x]
    return out


@app.post("/mark", response_class=HTMLResponse)
def create_mark(
    request: Request,
    day: str = Form(...), start: str = Form(...), end: str = Form(...),
    threshold: float = Form(BASELINE_THRESHOLD),
    type: str = Form(...),
    ids: list[str] = Form(default=[]),
    ids_csv: str = Form(default=""),
    note: str = Form(default=""),
):
    article_ids = _collect_ids(ids, ids_csv)
    try:
        marks.add_mark(day, type, article_ids, note=note or None)
    except ValueError:
        pass  # too-few ids etc. — ignore and just re-render
    return _render_window(request, day, start, end, threshold)


@app.post("/mark/delete", response_class=HTMLResponse)
def remove_mark(
    request: Request, mark_id: str = Form(...),
    day: str = Form(...), start: str = Form(...), end: str = Form(...),
    threshold: float = Form(BASELINE_THRESHOLD),
):
    marks.delete_mark(mark_id)
    return _render_window(request, day, start, end, threshold)
