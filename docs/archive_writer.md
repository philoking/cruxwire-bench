# Archive-writer module for cruxwire (the public-repo half)

This document specifies the small, self-contained feature that lands in
**cruxwire** (the public repo). The bench never ships code into cruxwire — this
is the contract the cruxwire-side module conforms to. Single-source of truth for
the record format is [`schema/corpus_schema.py`](../schema/corpus_schema.py) in
this repo.

## Design constraints (from the spec + cruxwire's reality)

- **Pure stdlib.** cruxwire advertises "no Python dependencies (pure stdlib)".
  The writer therefore emits **JSONL** (`json` module), not Parquet. The bench
  owns any Parquet/DuckDB conversion. This is the agreed resolution to the spec's
  "Storage format" open question, chosen to preserve cruxwire's zero-dep ethos.
- **One module, one call site.** Put it in a `corpus_archive` package (or a single
  `corpus_archive.py`) and call it from exactly one place in the ingestion path,
  importing nothing from the bench.
- **Off by default.** Gated by `BENCH_CAPTURE_ENABLED` (env, default false).
- **Best-effort, non-fatal.** Wrap the whole write in try/except; log and swallow.
  It can never raise into the production path or drop a real article.

## Where to call it

In `pipeline.py`, the cluster step already produces enriched articles with
`id, title, url, source, score, summary, image, published_at, embedding,
cluster_id, cluster_size, cluster_rep`. The natural call site is **right after
`cluster(...)` assigns `cluster_id`**, so each record can carry its
`prod_cluster_id` — the baseline recorded, not inferred. One call:

```python
# pipeline.py, after cluster(articles, ...) returns, behind the flag:
from corpus_archive import archive_articles   # single import, single module
archive_articles(articles, params=current_clustering_params())  # best-effort inside
```

## Record shape (one JSON object per line)

Conforms to `schema/corpus_schema.py` (SCHEMA_VERSION there is authoritative).
Required: `schema_version, article_id, day, block_id`. Everything else nullable
but should be filled when available:

```json
{
  "schema_version": 2,
  "article_id": "<cruxwire article id>",
  "day": "2026-06-24",
  "block_id": "0800",
  "source": "Reuters",
  "title": "...",
  "url": "https://...",
  "published_at": "2026-06-24T08:00:00Z",
  "ingested_at": "2026-06-24T08:02:11Z",
  "score": 7.4,
  "has_image": true,
  "body_text": "full text used for re-embedding under other models",
  "embedding": [/* 768 floats, nomic-embed-text */],
  "embedding_model": "nomic-embed-text",
  "embedding_model_version": "<bump on model/config change>",
  "entities": ["Fed", "ECB"],
  "prod_cluster_id": "<the cluster_id cruxwire assigned>",
  "prod_params": {"sim_threshold": 0.82, "boost_cap": 1.0, "boost_k": 0.5}
}
```

### Two fields the spec's Data Model omitted — include them

cruxwire's `cluster()` anchors each story on its **highest-scoring** article and
processes in **score-descending** order; the representative tie-break is
"score, then has-image". So faithful replay of `prod_cluster_id` is impossible
without:

- **`score`** — the cruxwire relevance score (0–10).
- **`has_image`** — whether the article had an image (rep tie-break).

These are why the bench schema is at `SCHEMA_VERSION = 2`. See
[SPEC_REVIEW.md](../SPEC_REVIEW.md).

## File layout on the shared volume

```
corpus/day=YYYYMMDD/block=HHMM/part-<seq>.jsonl
```

- Append a new `part-*.jsonl` per ingest run (never rewrite an existing part) —
  appending is lock-free, so the always-on app writer and always-on console
  reader never contend.
- `day` / `block_id` are also stored inline in each record, so selection works
  even if the directory layout drifts.

## `block_id` derivation (spec open question)

Bucket from `ingested_at` into 2-hour blocks: `block_id = "%02d00" % (hour // 2 * 2)`
(e.g. 08:00–09:59 → `0800`). If cruxwire has a clean per-update id, derive from
that instead and keep the `HHMM` form. Confirm the day-boundary timezone.

## Removal / disable

Deleting the `corpus_archive` module and its one call site, or simply leaving
`BENCH_CAPTURE_ENABLED` false, returns ingestion to byte-for-byte its current
behavior. The bench then just stops seeing new days.
