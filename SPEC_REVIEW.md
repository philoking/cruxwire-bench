# Spec review — findings from reading cruxwire's actual code

Notes from reviewing `console_spec.md` against the real
[philoking/cruxwire](https://github.com/philoking/cruxwire) `pipeline.py`. The
spec is solid and internally coherent; these are the places where the live code
diverges from spec assumptions, plus a few decisions worth recording.

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
