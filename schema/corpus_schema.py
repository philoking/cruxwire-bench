"""Corpus-archive record schema — the versioned interface between cruxwire and the bench.

One row per ingested article, written by cruxwire (best-effort, behind a flag),
read by the bench. Mirrors console_spec.md → "Data Model → Corpus archive record",
with two additions justified by reading cruxwire's actual `cluster()` (see NOTE
below and SPEC_REVIEW.md):

    * `score`        — cruxwire anchors clusters on the *highest-scoring* article
                       and processes in score-descending order. Faithful replay of
                       `prod_cluster_id` is impossible without it. The spec's Data
                       Model omitted it; it is required for replay fidelity.
    * `has_image`    — cruxwire's representative tie-break is "higher score, then
                       has-image". Needed to reproduce which member is the rep.

Bump SCHEMA_VERSION on every additive change. The bench warns at startup if it
sees a record whose schema_version is newer than this constant.
"""

from __future__ import annotations

# v1: spec Data Model fields.
# v2: added `score` and `has_image` (required to faithfully replay cruxwire's
#     score-anchored clustering — see module docstring and SPEC_REVIEW.md).
# v3: added `summary`; demoted `body_text` to optional. Verified on production
#     (nova): cruxwire embeds `title + "\n" + summary` and NEVER persists body
#     text. `summary` is therefore both available and the exact embed-input, so
#     re-embedding a candidate model on title+summary is the fair comparison.
SCHEMA_VERSION = 3

# cruxwire today: EMBED_MODEL='nomic-embed-text', 768-dim. Sizes the embedding
# column. (Spec Open Question "Production embedding dimension" — confirm 768.)
PROD_EMBEDDING_MODEL = "nomic-embed-text"
PROD_EMBEDDING_DIM = 768

# Field name -> (python kind, nullable, note). The bench selects the columns it
# knows and ignores newer unknown ones (Parquet/DuckDB tolerate extra columns).
CORPUS_FIELDS: dict[str, tuple[str, bool, str]] = {
    "schema_version":          ("int",       False, "bumped on additive changes; bench warns if newer"),
    "article_id":              ("text",      False, "cruxwire article 'id' (stable_id(url)); NOT globally unique — recurs across blocks via carry-forward. Natural key is (article_id, day, block_id)"),
    "day":                     ("date",      False, "YYYY-MM-DD the article belongs to, for day selection"),
    "block_id":                ("text",      False, "the 2-hour run block, e.g. '0800'; bucketed from the run's generated_at. Spans are contiguous blocks"),
    "source":                  ("text",      True,  "outlet / feed"),
    "title":                   ("text",      True,  "shown in the window view, searchable; first line of the embed input"),
    "summary":                 ("text",      True,  "cruxwire's 1-2 sentence summary; second line of the embed input (v3)"),
    "url":                     ("text",      True,  ""),
    "published_at":            ("timestamp", True,  "from the article"),
    "ingested_at":             ("timestamp", True,  "run-level: the run's generated_at (cruxwire stores no per-article ingest time)"),
    "score":                   ("double",    True,  "cruxwire relevance score 0-10; anchor ordering + rep tie-break (v2)"),
    "has_image":               ("bool",      True,  "rep tie-break: score, then has-image (v2)"),
    "body_text":               ("text",      True,  "OPTIONAL: not persisted by cruxwire today; embed input is title+summary, not body (v3)"),
    "embedding":               ("float[]",   True,  f"production embedding, {PROD_EMBEDDING_DIM}-dim for nomic-embed-text"),
    "embedding_model":         ("text",      True,  "e.g. nomic-embed-text"),
    "embedding_model_version": ("text",      True,  "bump when the production model or config changes"),
    "entities":                ("text[]",    True,  "people/orgs/places if available; nullable, backfillable"),
    "prod_cluster_id":         ("text",      True,  "cluster cruxwire assigned (its cluster_id = rep article id)"),
    "prod_params":             ("json",      True,  "snapshot of clustering params in effect at ingest"),
}

REQUIRED_FIELDS = [name for name, (_, nullable, _n) in CORPUS_FIELDS.items() if not nullable]

# Mapping the python kinds above to DuckDB column types, used when the bench
# materializes JSONL into a typed Parquet/DuckDB table.
_DUCKDB_KIND = {
    "int":       "INTEGER",
    "text":      "VARCHAR",
    "date":      "DATE",
    "timestamp": "TIMESTAMP",
    "double":    "DOUBLE",
    "bool":      "BOOLEAN",
    "float[]":   f"FLOAT[{PROD_EMBEDDING_DIM}]",
    "text[]":    "VARCHAR[]",
    "json":      "JSON",
}

DUCKDB_COLUMN_TYPES: dict[str, str] = {
    name: _DUCKDB_KIND[kind] for name, (kind, _nul, _note) in CORPUS_FIELDS.items()
}


def validate_record(rec: dict) -> list[str]:
    """Return a list of human-readable problems with a single corpus record.

    Empty list == valid for the columns we understand. Tolerant of *extra*
    keys (newer schema), strict about missing required keys and the embedding
    dimension when an embedding is present.
    """
    problems: list[str] = []
    for f in REQUIRED_FIELDS:
        if rec.get(f) in (None, ""):
            problems.append(f"missing required field: {f}")

    emb = rec.get("embedding")
    if emb is not None:
        if not isinstance(emb, (list, tuple)):
            problems.append("embedding must be a list of floats")
        elif rec.get("embedding_model", PROD_EMBEDDING_MODEL) == PROD_EMBEDDING_MODEL \
                and len(emb) != PROD_EMBEDDING_DIM:
            problems.append(
                f"embedding has {len(emb)} dims, expected {PROD_EMBEDDING_DIM} for {PROD_EMBEDDING_MODEL}"
            )

    sv = rec.get("schema_version")
    if isinstance(sv, int) and sv > SCHEMA_VERSION:
        problems.append(f"record schema_version {sv} is newer than this bench handles ({SCHEMA_VERSION})")
    return problems
