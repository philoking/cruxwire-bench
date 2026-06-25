# Cruxwire Clustering Bench: Spec (v1)

Working name: **Clustering Bench** (the console). Its unit of work is the **window**, a sizable span of one or more 2-hour blocks within a day.

## Summary

A standalone analysis console for debugging and tuning cruxwire's clustering, running in its own container and its own repo, separate from the reader app. The app gains one small, self-contained feature, behind an off-by-default flag, that writes a copy of every ingested article (text plus embedding plus metadata) to a corpus archive on a shared volume. The console recreates any past window from that archive, where a window is a span the operator sizes from a single 2-hour block up to a whole day of accumulated stories. It shows the full clustering for that span (clusters and the unclustered singletons together), lets the operator mark mistakes in place, then re-runs clustering on the same or a wider span with tweaked parameters, and (a major use) with a different embedding model, showing how the result moves against both the baseline and the operator's marks. Nothing changes the reader experience for everyday users, and the interactive work never competes with production for the GPU.

## Problem Statement

Cruxwire's clustering works well enough day to day, but it cannot be improved systematically. The app updates on a 2-hour cadence and the operator keeps stories open all day, so stories accumulate and cluster against the growing set across the whole day. Clustering is a streaming, destructive process: articles arrive, get compared, merge or do not, and the context is gone. When the operator notices a mistake (a story that should have clustered and did not, or a cluster that swept in an unrelated story), there is no way to recreate the relevant span of stories, see the whole clustering, say what was wrong, and test whether a parameter or embedding-model change fixes it without breaking the rest, and no way to do so at the scale clustering actually runs at by end of day. Tuning today is spot-check and vibes.

## Goals

1. Recreate any past window as a frozen set of stories that can be re-clustered on demand, where the window is sized by the operator from one 2-hour block up to a full day.
2. Let the operator expand the window incrementally (add 2-hour blocks, up to all stories in the day), so behavior can be tested both in isolation and at the full accumulated scale production reaches.
3. Show the full clustering for the current span in one view: every cluster, every unclustered singleton, all stories in the span in one place.
4. Let the operator mark mistakes in place: inside a cluster, mark a story that does not belong; among singletons and across clusters, mark stories that should have been one cluster.
5. Re-run clustering on the same or a wider span with tweaked parameters and show how the result changes, scored against the operator's marks.
6. Compare alternative embedding models against the same marked span, using the marks as model-agnostic ground truth, so the choice of embedding model becomes a measured decision.
7. Run entirely out of the reader's path: zero change to the everyday user experience, no GPU contention during normal operation, and the app's contribution small enough to review, disable, or remove trivially.

## Non-Goals

- **Not a production change to clustering.** The bench never writes back to the live app. Promoting a tuned parameter set or a new embedding model to production is a manual, separate step out of scope here.
- **Not cross-day analysis.** The working unit is a span within a single day. Multi-day spans and long-range trend tracking are out of scope for v1.
- **Not the LLM-as-judge audit.** A related but separate tool. The bench produces the corpus and the operator's marks the audit will later consume.
- **Not a public or multi-user feature.** Marking is the operator's private signal. A reader-facing "looks wrong" signal is a different product, explicitly out of scope.
- **Not a learned merge model.** The marks are eventual training data for one, but the bench does not train or serve it in v1.
- **Not a backfill of history.** The corpus begins the day capture is turned on.

## Architecture Overview

Three layers, mapping onto three cadences:

1. **Corpus archive (continuous, GPU work already done).** When the flag is on, the app appends each ingested article plus its already-computed production embedding plus metadata to an append-only archive on a shared volume, tagged with the 2-hour block it was ingested in. Best-effort side effect of ingestion, the only touch point between app and bench.
2. **Bench engine (on demand, CPU only).** Recreating a span and re-clustering it with arbitrary parameters is arithmetic over vectors that already exist. No model invocation, no GPU. Even a full day clusters exactly via a vectorized similarity matrix (see Window Clustering Is Exact).
3. **GPU batch (occasional, deliberate).** Two jobs need the GPU: re-embedding the corpus text under an alternative embedding model (see Testing Embedding Models), and extracting a new signal such as entity vectors. Both are explicit overnight batch runs on Nova, never fired interactively.

The two pieces live in two repos and two containers, joined only by a versioned file format and a shared volume (see Repos and Build Layout).

```
  cruxwire repo (public)                bench repo (private)
  reader app container                  console container
  ┌──────────────────────┐             ┌──────────────────────────┐
  │ ingestion (2h cadence)│            │ web UI: day + span control│
  │  - JSON files (live,  │            │   window view, mark,      │
  │    unchanged)         │            │   re-run, model compare   │
  │  - [flag] corpus_     │  shared    │ bench engine (CPU)        │
  │    archive module ────┼──volume────►│ reads corpus archive (RO) │
  │    (one call site)    │  (RO here)  │ owns marks + embeddings   │
  └──────────────────────┘             └──────────────────────────┘
                                        GPU batch (re-embed / signals), overnight
```

## Repos and Build Layout

Two repos, weakly and one-directionally coupled. The app imports nothing from the bench and knows nothing about it; it only emits a file format.

**cruxwire (public, existing).** The app's whole contribution is a self-contained archive-writer feature: behind an off-by-default flag, it writes a self-describing corpus archive as a best-effort side effect of ingestion. This is a legitimate optional feature on its own terms ("cruxwire can archive what it ingested"), not a debugger hook, so it belongs in the public repo without caveat. Isolate it as a single module (for example a `corpus_archive` package) with exactly one call site in the ingestion path, so it is trivially reviewable, trivially disabled by the flag, and trivially removed. The reader UX never imports or references it.

**bench (private, new).** Engine, web UI, marks store, and the GPU batch jobs ship together as one repo and one console container. Do not fragment a solo tool into more repos than deploy boundaries demand. Host it in Gitea private for now (it is a personal, workflow-specific console, not portfolio surface), with the option to promote to GitHub later if it becomes worth showing. The split from cruxwire is driven by the public/private boundary, not by technical need: a private half-built tool should not nest inside the public showcase repo.

**The seam is the schema, not code.** Two independently deployed containers can drift, so treat the corpus archive format as a versioned interface:

- Every archive record carries a `schema_version`.
- Single-source the schema definition in the bench repo (the schema-sensitive consumer) and have the cruxwire writer conform to that spec. Do not stand up a third shared-schema repo or package; it will not earn its keep for one person.
- Evolve additively: new columns are nullable, existing columns are never repurposed. Parquet's columnar layout lets the bench select the columns it knows and ignore newer unknown ones.
- The bench warns at startup if it sees a `schema_version` newer than it handles. That one check is sufficient governance.

**The shared volume is the only deploy-level coupling.** Both compose files reference the same named volume (or a well-known host path): cruxwire mounts it read-write, the bench mounts it read-only. Note this covers only the corpus archive. The bench's marks store and its multi-model embeddings store are bench-private and live on the bench's own volume, not the shared one, which keeps the contract small.

Net: a feature in the public app, a separate private tool, and a versioned file-format contract plus a shared volume joining them. Nothing imports across the line, nothing deploys in lockstep, and either side can be rebuilt or deleted without breaking the other beyond losing the archive.

## The Window (an expandable span)

The window is the central object, and it is sizable. The app tags each ingested article with the 2-hour block it arrived in. A window is a contiguous run of one or more of those blocks within a single day:

- **Minimum span:** one 2-hour block, matching the app's update granularity.
- **Expansion:** the operator adds blocks in 2-hour increments, or jumps straight to the whole day.
- **Maximum span:** the full day, every story still open across all of that day's updates.

Because the operator does not close out stories during the day, production clustering accumulates: each 2-hour update clusters new stories against the set still open from earlier. So the **full-day span is the faithful reproduction** of what the reader actually accumulated by end of day, and the narrower spans are deliberately reduced contexts. Expanding the span is therefore two things at once: a way to reproduce production at the scale it really runs, and a controlled experiment in how much context the clustering needs. A miss that only resolves once a later block is added tells you the match simply was not present yet at the smaller span; an over-merge that only appears at full-day scale tells you the parameters do not hold up as the set grows.

The operator picks a day, the console lists that day's blocks (with story counts), and the operator sets the span. Re-running at different spans is a primary activity, not an edge case.

## Window View (primary surface)

For the current span, the console shows the entire clustering result on one screen:

- **Clusters**, each a group of member stories (title, source, time), ordered by size or recency.
- **Singletons**, the stories in the span that clustered with nothing, in their own section. This is where missed merges are found.

Clusters and the stories that escaped clustering are visible together, so the operator can audit what merged and scan what did not, at whatever span is set. A span indicator shows which blocks are included and the total story count, with controls to add a block, drop a block, or expand to the full day, each of which re-clusters and refreshes the view.

**Marking, done in place.** Marks are recorded as relationships between stories (by article id), not as edits to a particular cluster, so a mark made at one span, parameter setting, or embedding model stays valid under any other:

1. **Same story (missed merge).** Select two or more stories that should be one cluster, whether singletons or sitting in different clusters, and mark them the same. Records a `same` mark over those ids.
2. **Not the same (over-merge / wrong member).** Inside a cluster, mark a story that does not belong with the rest. Records a `not_same` mark separating that story from the ids it was wrongly grouped with.
3. **Confirm (optional).** Affirm a cluster is correct, recording a `confirm` mark over its member ids.

Marking is one or two actions with no required categorization in the moment. Cause tagging and review happen later.

## Re-Run (the loop)

From the current span, the operator changes clustering parameters (threshold, time window, linkage rule, entity-overlap weight, candidate-generation method, and embedding model) and re-runs clustering on that frozen set. The console then shows:

- **The new clustering** for the span, in the same clusters-plus-singletons view.
- **What changed vs the baseline** for this span: which stories merged that were separate, which clusters split, which stories moved. Span-scoped, so it stays readable.
- **Score against the marks.** Of the `same` marks, how many the new run now satisfies; of the `not_same` marks, how many are now honored; and how much unmarked collateral the change introduced. Because marks are relational over ids, the score is computed identically at any span, parameter set, or embedding model.

The operator iterates across the axes: tweak parameters, widen or narrow the span, and swap embedding model. The configuration to promote is the one that satisfies the marks with minimal collateral at the full-day span, since that is the scale production runs at.

## Testing Embedding Models

This is a first-class capability, not an afterthought, and the design makes it cheap to get right.

**The app's contribution does not change.** Cruxwire only ever writes its one production embedding (currently nomic-embed-text) into the corpus archive, as it already does. Alternative embeddings are produced entirely bench-side, so adding model comparison touches nothing in the public repo and adds nothing to the app's footprint.

**Alternative embeddings come from a deliberate GPU batch job.** To test model M, the bench reads the corpus text for a day (or a wider corpus) and runs M over it via your Ollama instance on Nova, writing the resulting vectors into a bench-private embeddings store keyed by article id, model, and version. This is the only GPU work, run overnight, never interactive, so it never competes with production. Once computed, the vectors are reused for every subsequent re-run, so clustering under M stays CPU-only just like production.

**Embedding model is a clustering-run parameter.** Selecting a model in a re-run means the engine clusters the span using that model's stored vectors. Everything else (span, view, diff, scoring) is unchanged.

**Thresholds are per-model, and comparisons must be fair.** Cosine values are not comparable across models: a 0.82 threshold under nomic-embed-text means nothing under another model with a different similarity distribution. So comparing models honestly means tuning each model's threshold to its own best operating point against the marks first, then comparing models best-against-best. Comparing a tuned model to a detuned one is the trap to avoid. The bench supports a per-model threshold sweep scored against the day's marks (this is the same threshold-curve machinery used for parameter tuning), reports each model's best operating point, and then ranks models by marks satisfied and collateral introduced at their respective best points.

**The marks are the model-agnostic benchmark, and that is the unlock.** A `same` or `not_same` mark is a fact about two stories, independent of any embedding. So a marked day is a fixed evaluation set you can run any candidate model through. This is what makes model selection a measurement instead of a vibe: pick a few well-marked full days, re-embed them under each candidate, sweep each model's threshold against the marks, and read off which model captures the most missed merges and avoids the most wrong merges.

**It also turns the embedding-failure diagnosis into a fix path.** When two stories marked `same` refuse to merge at any threshold under the production model, that is an embedding failure (genuinely far apart in vector space), and the high-entity-overlap / low-cosine pairs surfaced in the singleton list (P1-2) are exactly that set. Re-embedding those days under a candidate model and checking whether it places those pairs close is the direct test of whether a model change actually fixes the failures you care about.

Swapping production's live embedding model is a larger move (re-tune the live threshold, re-embed the live corpus) and stays a future consideration. The bench's role is to tell you, with evidence, whether that move is worth making.

## Window Clustering Is Exact (design note)

Even a full day of accumulated stories is a bounded set, and at that scale the engine clusters exactly rather than approximately. Compute the full pairwise cosine similarity as a single normalized matrix product (L2-normalize the span's embeddings into a matrix X, then X times X transpose), which BLAS handles fast, rather than a Python pairwise loop. Rough scale: a few thousand stories produces a similarity matrix of a few tens of megabytes and clusters in about a second; the n-squared cost stays comfortable through realistic full-day volumes (single-digit thousands of stories). Past roughly ten thousand stories the matrix and time grow enough that the engine would need blocking or approximate retrieval, but that is beyond a single day at expected feed volume. This holds for any embedding model, with the matrix width set by that model's dimensionality.

Two consequences of exact clustering:

- Within any span, the operator can always see the true cosine between any two stories, so a missed merge is never hidden behind "they were never retrieved as candidates." Any miss is a threshold, weighting, or embedding issue the operator can reason about directly.
- When two stories marked `same` still refuse to merge at any sane threshold, that is the diagnosis itself: their embeddings are genuinely far apart, so the embedding model failed on this pair, not the threshold, which is precisely the case Testing Embedding Models exists to resolve.

## Storage and Concurrency

The reader app continues to read and write its JSON files exactly as today; untouched. The corpus archive is a separate, write-from-app / read-from-console artifact on the shared volume.

Two long-running containers share the archive: the app is effectively an always-on writer, the console runs a live web service holding read connections. This is the one real concurrency case, because a single DuckDB file does not allow a cross-process writer and reader to camp on it at once.

**Recommended approach (lock-free):** ingestion appends to a block-partitioned Parquet dataset on the shared volume (for example `corpus/day=YYYYMMDD/block=HHMM/part-*.parquet`). The console queries that Parquet directly with DuckDB. Appending a Parquet file takes no lock anyone holds, DuckDB reads Parquet natively, and the partition layout means selecting a span is just reading its blocks' partitions. Same Parquet-plus-DuckDB pattern already in use for the Home Assistant cold tier.

**Acceptable alternative:** the app writes a DuckDB file directly and the console opens it strictly read only. Workable but more fragile under two always-on containers.

Two invariants hold either way:

- **Best effort and non-fatal.** A failure writing the corpus copy is caught, logged, and swallowed inside ingestion. It can never raise into the production path or drop a real article from the feed.
- **Effectively always on.** Capture is a deploy-time setting, not a per-session toggle. A span with missing stories cannot be faithfully recreated. The corpus only knows days ingested since the flag was enabled, which is acceptable.

The bench's own stores (marks, and the multi-model embeddings store) live on the bench's private volume, not the shared one.

## Data Model

### Corpus archive record (one row per ingested article, written by the app; this is the versioned contract)

| field | type | notes |
|---|---|---|
| `schema_version` | int | bumped on additive schema changes; bench warns if newer than it handles |
| `article_id` | text | stable id, primary key |
| `day` | date | the day this article belongs to, for day selection |
| `block_id` | text | the 2-hour ingest block; spans are built from contiguous blocks |
| `source` | text | outlet / feed |
| `title` | text | shown in the window view, searchable |
| `url` | text | |
| `published_at` | timestamp | from the article |
| `ingested_at` | timestamp | ordering key for faithful replay (see Replay Fidelity) |
| `body_text` | text | or `body_path` if stored externally; needed for re-embedding under other models |
| `embedding` | FLOAT[768] | the production embedding, fixed at the production model's dimensionality |
| `embedding_model` | text | for example `nomic-embed-text` |
| `embedding_model_version` | text | bump when the production model or config changes |
| `entities` | list<text> | extracted people / orgs / places if available; nullable, backfillable later |
| `prod_cluster_id` | text | the cluster production assigned this article to, so the baseline is recorded not inferred |
| `prod_params` | json | snapshot of clustering params in effect at ingest |

### Embeddings store (bench-private, derived, multi-model)

Holds one row per (article, model, version), so the same article can carry vectors from several models. Production-model rows are seeded from the archive's `embedding`; alternative-model rows are produced by the GPU batch job. Partition by model so differing dimensionalities each sit in their own consistently shaped partition.

`article_id`, `embedding_model`, `embedding_model_version`, `dim`, `embedding` (list<float>).

### Run record (one row per re-run the console executes)

`run_id`, `day`, `span` (start block, end block), `embedding_model`, `created_at`, `params` (json), `is_baseline` (bool), `notes`.

### Mark record (written by the operator; console owns this store)

| field | type | notes |
|---|---|---|
| `mark_id` | text | |
| `day` | date | the day being audited |
| `created_at` | timestamp | |
| `type` | enum | `same`, `not_same`, `confirm` |
| `article_ids` | list<text> | the stories the relationship holds over; `same` can be three or more |
| `note` | text | optional one line |
| `status` | enum | `new` then `reviewed` |
| `cause` | enum | filled at review: `embedding_miss`, `threshold_miss`, `sparse_entities`, `topic_vs_story`, `other` |

Marks are relationships over article ids, deliberately not tied to a cluster id, span, parameter set, or embedding model, so they remain valid as the operator changes any of those.

## Replay Fidelity (important)

Streaming clustering is order dependent: the same stories processed in a different order can produce different clusters. A faithful baseline re-run of a span must process its stories in original `ingested_at` order using production params and the production embedding, and should reproduce the recorded `prod_cluster_id`. The full-day baseline is the one to trust most, since it matches what production accumulated. The console verifies reproduction on load and surfaces any divergence, because if the baseline does not reproduce, every comparison against it is suspect.

## User Stories

Single operator (the author).

- As the operator, I want to pick a day and start with a single 2-hour block, so I can look at the smallest unit where I saw a mistake.
- As the operator, I want to expand the window block by block, or jump to the whole day, so I can test clustering at the scale it actually runs by end of day.
- As the operator, I want to see the current span's full clustering in one view, clusters and unclustered singletons together.
- As the operator, when I open a cluster, I want to mark a story that does not belong in one tap.
- As the operator, scanning the singletons, I want to select stories that should have been one cluster and mark them the same.
- As the operator, I want my marks to stay valid when I change parameters, widen the span, or swap embedding model, so I do not have to re-mark as I test.
- As the operator, I want to change parameters and re-run the span, and see the result scored against my marks.
- As the operator, I want to re-embed a marked day under a different model and compare it to production at each model's best threshold, so I can decide whether the model is worth switching to.
- As the operator, later and separately, I want to review my marks and tag a cause.

## Requirements

### Must-Have (P0)

**P0-1 Feature-flagged corpus archive, as an isolated module with a versioned schema.**
- Given the flag is enabled, when an article is ingested, then a corpus record with all required fields including `schema_version`, `embedding`, `embedding_model_version`, `day`, and `block_id` is appended to the shared archive.
- Given the corpus write fails, then it is logged and swallowed and the article is still served normally.
- Given the flag is disabled, then ingestion behavior is byte-for-byte unchanged.
- The writer is a single module with one call site in the ingestion path, importing nothing from the bench.

**P0-2 Standalone console container in its own repo.**
- Given the console is up while the app ingests, when the operator runs queries, then neither side blocks or corrupts the other.
- Given the console reads an archive whose `schema_version` is newer than it handles, then it warns at startup and proceeds with the columns it understands.

**P0-3 Day selection and span sizing.**
- Given a day, then the console lists that day's 2-hour blocks with story counts.
- Given a chosen start and end block, or "whole day", when the operator sets the span, then the engine clusters exactly that set and the view refreshes.
- Given a current span, when the operator adds a block, drops a block, or expands to full day, then the span re-clusters accordingly.

**P0-4 Window view: clusters plus singletons.**
- Given a span, then the console shows every cluster with its members and every singleton on one surface, with the span and total story count indicated.
- Given a baseline re-run reproducing production, then the displayed clustering matches recorded `prod_cluster_id` within a reported divergence.

**P0-5 In-place marking, relational and stable.**
- Given an open cluster, when the operator marks a member as not belonging, then a `not_same` mark is stored.
- Given two or more selected stories, when the operator marks them the same, then a `same` mark over those ids is stored.
- Marks remain valid across parameter, span, and embedding-model changes. No categorization required at mark time.

**P0-6 Re-run on the current span.**
- Given a parameter set, when the operator re-runs, then clustering recomputes exactly over the current span, CPU only, and a run record is produced.

**P0-7 Change view and mark scoring.**
- Given a re-run and the span baseline, then the console shows the span-scoped change and a score against the day's marks: `same` marks now satisfied, `not_same` marks now honored, and unmarked collateral changes.

### Nice-to-Have (P1)

**P1-1 Mark review.** A separate surface to set status and tag a cause on each mark.

**P1-2 Entity-overlap surfacing.** In the singleton list, flag pairs with high entity overlap but low cosine: the likely embedding-failure misses, and the natural test set for a model swap.

**P1-3 Embedding-model testing.**
- A GPU batch job that, given a candidate model and a day (or wider corpus), runs the model over the stored text via Ollama and writes vectors to the bench embeddings store tagged with model, version, and dim. Overnight, never interactive.
- The embedding model is selectable as a re-run parameter; the engine clusters using that model's vectors.
- A per-model threshold sweep scored against the day's marks, reporting each model's best operating point, and a best-against-best comparison ranking candidate models by marks satisfied and collateral introduced.

**P1-4 Multi-day evaluation.** Run a candidate configuration (params and model) against the full-day spans of several marked days and aggregate the mark scores, so a promotion decision is not based on one day.

**P1-5 Search within a day.** Find a story by remembered title or source within the selected day.

### Future Considerations (P2)

**P2-1 Export labeled examples** in the shape the future LLM-as-judge audit and a learned model will consume.
**P2-2 Learned merge model** trained on accumulated marks.
**P2-3 Production embedding-model swap support** (live re-embed, live threshold re-tune), informed by the bench's comparisons.
**P2-4 Blocked/approximate clustering** if span sizes ever exceed the exact ceiling.

## Deployment

- **Two repos:** cruxwire (public, the archive-writer module behind a flag) and bench (private, Gitea, the console plus engine plus batch jobs).
- **Shared volume** holding the corpus archive, mounted read-write by the app and read-only by the console. This is the only deploy-level coupling.
- **App change:** one config flag (for example `BENCH_CAPTURE_ENABLED=true`) plus the isolated archive-writer module. Default off.
- **Console container:** web UI, bench engine, marks store, and embeddings store, the latter two on the bench's own volume.
- **GPU batch jobs** (re-embed, signal extraction) run as separate scheduled invocations on Nova when idle, hitting Ollama for embeddings.
- Bring-up is "enable the flag, spin up the second container, point it at the shared volume," with no migration of existing JSON data.

## Open Questions

- **Production accumulation behavior (engineering, blocking):** confirm production clusters cumulatively across the open day (so the full-day span is the faithful baseline) rather than re-clustering each 2-hour batch independently. This validates the expandable-span model.
- **Storage format (engineering, blocking):** Parquet-over-DuckDB (recommended) vs a directly written read-only DuckDB file.
- **Block alignment (engineering, blocking):** is there a clean per-update id to derive `block_id` from, or is it bucketed from `ingested_at`?
- **Schema home and ownership (engineering, blocking):** confirm the schema is single-sourced in the bench repo with the cruxwire writer conforming, and agree the additive-only evolution rule.
- **Production embedding dimension (engineering, blocking for schema):** confirm 768 for the current nomic-embed-text config, sizing the archive `embedding` column.
- **Candidate models to test (product, non-blocking):** which alternative embedding models are worth pulling into Ollama for comparison, and at what corpus scope (a few marked days vs more).
- **Day boundary (engineering, non-blocking):** which timezone's midnight, and how a story open across midnight is assigned a `day`.
- **Body storage (engineering, non-blocking):** inline `body_text` vs external `body_path`. Note that keeping the text is required for re-embedding under other models, so it cannot be dropped.

## Suggested Phasing

1. **Phase 1 (recreate and size a window):** P0-1, P0-2, P0-3, P0-4. The archive-writer feature lands in cruxwire, the bench repo and container stand up, and any day since capture began can be opened, sized from one block to the full day, and its clustering viewed.
2. **Phase 2 (mark and re-run):** P0-5, P0-6, P0-7. The full parameter-tuning loop works: mark mistakes at any span, tweak params, re-run, widen to full day, read the score.
3. **Phase 3 (sharpen and compare models):** P1-1 review and cause tagging, P1-2 embedding-failure surfacing, P1-3 embedding-model testing, P1-4 multi-day evaluation. After this, both parameter and embedding-model decisions rest on evidence across several marked days, and the marks become a clean labeled set for the future audit and model.
