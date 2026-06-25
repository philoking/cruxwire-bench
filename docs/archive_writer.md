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

## Where to call it (verified against the real run_once on nova)

`run_once()` already assembles everything the archive needs near the end:
`scored` (the clustered articles, each with `id, title, summary, source, score,
image, published_at, cluster_id, cluster_size, cluster_rep`) and `emb_index`
(`{id: embedding}`, kept just before embeddings are stripped from the digest).

The natural call site is **right where `digest.json` / `embeddings.json` are
written**, joining the two. One call, behind the flag:

```python
# pipeline.py, just before/after _atomic_write_json(DIGEST_FILE, ...):
from corpus_archive import archive_articles      # single import, single module
archive_articles(scored, emb_index, generated_at, cfg)   # best-effort inside
```

`block_id` is bucketed from `generated_at` (the run timestamp); `ingested_at` is
that same timestamp (cruxwire keeps no per-article ingest time). The writer joins
each article's embedding from `emb_index` by id and reads `summary`/`image`/`score`
straight off the article.

### Which articles to archive — the open decision

`scored` is the **post-retention** set at the write site (what the reader sees).
That gives an exact, small per-run reproduction. If you also want the
**pre-retention** pool (stories that were pruned but might have clustered),
capture `scored` *before* `apply_retention` runs and tag the records. See the
"one decision for the operator" in [SPEC_REVIEW.md](../SPEC_REVIEW.md). Until
that's settled, archiving the post-retention digest is the safe default — it is
exactly what `digest.json` + `embeddings.json` already contain.

> Note: a story carried forward across N runs is archived N times (once per
> block) with the `cluster_id` it had in each run. That is intended — `block_id`
> distinguishes them and the bench dedups per span. `article_id` is therefore
> NOT unique across blocks; the natural key is `(article_id, day, block_id)`.

## Record shape (one JSON object per line)

Conforms to `schema/corpus_schema.py` (SCHEMA_VERSION there is authoritative).
Required: `schema_version, article_id, day, block_id`. Everything else nullable
but should be filled when available:

```json
{
  "schema_version": 3,
  "article_id": "<cruxwire article id = stable_id(url)>",
  "day": "2026-06-24",
  "block_id": "0800",
  "source": "Reuters",
  "title": "...",
  "summary": "the 1-2 sentence summary; title + \"\\n\" + summary is the embed input",
  "url": "https://...",
  "published_at": "2026-06-24T08:00:00Z",
  "ingested_at": "2026-06-24T08:00:09Z",
  "score": 7.4,
  "has_image": true,
  "body_text": null,
  "embedding": [/* 768 floats, nomic-embed-text, joined from emb_index by id */],
  "embedding_model": "nomic-embed-text",
  "embedding_model_version": "<bump on model/config change>",
  "entities": ["Fed", "ECB"],
  "prod_cluster_id": "<the cluster_id cruxwire assigned this run>",
  "prod_params": {"sim_threshold": 0.82, "boost_cap": 1.0, "boost_k": 0.5}
}
```

### Two fields the spec's Data Model omitted — include them

cruxwire's `cluster()` anchors each story on its **highest-scoring** article and
processes in **score-descending** order (the raw `score`, before taste/cluster
boosts); the representative tie-break is "score, then has-image". So faithful
replay of `prod_cluster_id` is impossible without:

- **`score`** — the cruxwire relevance score (0–10), as stored on the article.
- **`has_image`** — whether the article had an image (rep tie-break).

`summary` (v3) is needed so a candidate model can re-embed on the **same input
text production used** (`title + "\n" + summary`) — body text is never stored.
See [SPEC_REVIEW.md](../SPEC_REVIEW.md).

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
