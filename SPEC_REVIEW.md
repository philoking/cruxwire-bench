# Spec review — findings from reading cruxwire's actual code

Notes from reviewing `console_spec.md` against the real
[philoking/cruxwire](https://github.com/philoking/cruxwire) `pipeline.py`. The
spec is solid and internally coherent; these are the places where the live code
diverges from spec assumptions, plus a few decisions worth recording.

---

## ★ Verified against production (nova, 2026-06-25)

Read-only inspection of the live `cruxwire-data` volume on nova confirmed the
blocking open questions and surfaced two findings that **reshape the spec's
central model**. These supersede the speculative notes further down where they
differ.

**Confirmed**
- **Embedding dim = 768**, model `nomic-embed-text`. Schema sized correctly.
- Digest article fields present: `score`, `image`, `summary`, `cluster_id`,
  `cluster_size`, `cluster_rep`, `taste_boost`, `cluster_boost`. So `score` and
  image (→`has_image`) are available, vindicating SCHEMA_VERSION 2.
- **No `body_text` and no per-article `ingested_at` are persisted.** `summary`
  is. → SCHEMA_VERSION 3: store `summary` (the real embed input), demote
  `body_text` to optional, treat `ingested_at` as the run's `generated_at`.

**Finding A — clustering is BATCH, not streaming (contradicts the Problem Statement).**
The spec's premise ("clustering is a streaming, destructive process… the context
is gone") is factually wrong. `run_once()` re-clusters the **entire pool from
scratch every 2h run** via a stateless `cluster()` call. Nothing is destroyed
incrementally; each run's full result is in `digest.json`. *Good news:* replay is
far more tractable than the spec feared — reproducing a run is just
`cluster(that run's pool)`. The motivating framing in the spec should be
corrected to "the embeddings and the pre-overwrite pool aren't durably kept,"
which is the real gap the archive fills.

**Finding B — retention caps the pool, so "full day" ≠ an ever-growing set (BLOCKING design issue).**
`runs.json` shows the retained digest holding steady at **~60 clusters / 70–140
articles every run**, all day — not growing into the thousands. `apply_retention`
prunes whole stories to a rank-weighted ceiling each run. Carry-forward *does*
accumulate unread stories, but retention bounds the result. Consequences:
  - The "full-day accumulated set" production actually clustered at end of day is
    the **last run's pool** (~90 stories), because carry-forward already folded
    the day into it. **The faithful full-day baseline is the last block's
    snapshot, NOT the union of every block.**
  - Unioning all blocks (the spec's "expand the span" taken literally) clusters a
    set **no production run ever saw** (it includes stories pruned mid-day and
    double-counts carried-forward ones). That's a legitimate *experiment*, but it
    is not a "faithful reproduction." The bench now dedups a multi-block span by
    `article_id` and labels single-block spans as the faithful unit
    (`corpus.load_span`).
  - **The n² "Window Clustering Is Exact" worry is moot at current settings:**
    production never clusters more than ~150 vectors at once. The exact matrix is
    trivially fast; the *pre-retention union* a few thousand only matters if you
    deliberately run that experiment.

**→ The one decision for the operator (see handoff):** should the archive capture
the **post-retention digest** each run (what the reader saw — exact per-run
reproduction, small), the **pre-retention clustered pool** (everything that could
have merged, bigger), or **both**? This determines what "the corpus" means and
how the window/span UI should frame the full-day view.

## 1. Replay order: score-descending, not `ingested_at` order (blocking for fidelity)

The spec's **Replay Fidelity** section says a faithful baseline re-run "must
process its stories in original `ingested_at` order." cruxwire's `cluster()`
does **not** do this. It:

- processes embedded articles in **descending `score`** order,
- **anchors** each story on its highest-scoring article (an article joins the
  nearest existing anchor above `SIM_THRESHOLD`, else starts a new one),
- is explicitly **not** single-link union-find (a comment notes this avoids
  transitive chaining A~B, B~C ⇒ A~C).

Consequences:
- The engine here reproduces the **real** algorithm (score order, anchored). The
  `corpus.load_span` query still orders rows by `ingested_at` for display/replay
  bookkeeping, but membership follows score order.
- **Faithful replay requires the article `score`**, which the spec's corpus
  record omitted. We added `score` (and `has_image`, for the rep tie-break) to
  the schema → `SCHEMA_VERSION = 2`. Without them, `prod_cluster_id` cannot be
  reproduced.

Recommendation: update the spec's Replay Fidelity wording to "score-descending,
anchored" and add `score`/`has_image` to its Data Model table.

## 2. "Linkage rule" as a re-run parameter (clarification)

The Re-Run section lists "linkage rule" among tunable parameters. Production has
exactly one linkage rule (anchored-on-rep). Offering single-link/union-find as an
alternative is a legitimate experiment, but note it is **not** what production
does, so a single-link run is not a "baseline" — it's an alternative. The engine
keeps anchored as the faithful baseline; other linkages can be added behind a
parameter without claiming baseline status.

## 3. Storage format vs cruxwire's zero-dependency promise (resolved)

The spec recommends Parquet-over-DuckDB. cruxwire advertises **pure stdlib, no
dependencies**. Writing Parquet from the app would force a pyarrow/duckdb
dependency into the public zero-dep app. Resolution (confirmed with the operator):
the **app writes JSONL** (stdlib `json`); the **bench** does any Parquet/DuckDB
work. DuckDB reads JSONL natively and appending JSONL is equally lock-free, so the
concurrency argument for Parquet still holds. See `docs/archive_writer.md`.

## 4. Baseline divergence check is threshold-sensitive (implementation note)

The window view's baseline check currently compares re-cluster co-membership to
`prod_cluster_id` **at the threshold the operator is viewing**. A true fidelity
check should always re-cluster at the **production** params/threshold (captured in
`prod_params`) regardless of the viewing threshold, and report divergence from
that. Wired as a TODO in `bench/web/app.py::_baseline_divergence`; needs
`prod_params` populated by the real archive-writer to be meaningful (synthetic
corpus leaves `prod_cluster_id` null, so the check no-ops).

## 5. Open questions from the spec — current stance

- **Production accumulation behavior** (blocking): cruxwire carries unread stories
  forward across the day (see `pipeline.py` carry-forward section), which supports
  the spec's "full-day span is the faithful baseline" model. Worth confirming that
  each 2h run clusters the *accumulated* pool, not just the new batch.
- **Production embedding dim**: cruxwire uses `nomic-embed-text`; schema assumes
  **768**. Confirm against the live model config before the archive-writer ships.
- **Body storage**: schema keeps `body_text` inline (required for re-embedding).
  cruxwire embeds `title + "\n" + summary`, *not* full body — so re-embedding a
  candidate model on `body_text` would change the input text vs production. To
  compare models fairly, re-embed on the **same text production embedded**
  (title+summary). Recommend the archive also store that exact embed-input, or
  the bench reconstructs `title + summary`. **New finding — not in the spec.**
- **Day boundary / block alignment**: bucket `block_id` from `ingested_at` (2h);
  confirm timezone. See `docs/archive_writer.md`.

## 6. Finding worth highlighting: embed-input text (new)

cruxwire embeds **`title + summary`**, not `body_text` (pipeline.py `enrich`).
The spec keeps `body_text` "for re-embedding under other models" — correct that
we need text, but for an apples-to-apples model comparison the candidate model
should embed the **same string production used**. Options: (a) store the exact
embed-input string in the archive, or (b) also store `summary` and have the bench
rebuild `title + "\n" + summary`. Recommend storing `summary` (cheap, also useful
in the UI). Flagged for a `SCHEMA_VERSION = 3` additive bump when the writer lands.
