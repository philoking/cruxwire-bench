# Cruxwire Clustering Bench

A standalone analysis console for debugging and tuning [cruxwire](https://github.com/philoking/cruxwire)'s
clustering. It recreates any past **window** — a span sized from a single 2-hour
block up to a whole accumulated day — from a corpus archive, shows the full
clustering (clusters **and** unclustered singletons together), lets the operator
mark mistakes in place, then re-runs clustering with tweaked parameters or a
different embedding model and scores the result against those marks.

It runs in its own container and (private) repo, separate from the reader app.
The only coupling is a **versioned file-format contract** plus a **shared volume**:
cruxwire writes a corpus archive (best-effort, behind an off-by-default flag);
the bench reads it read-only. Nothing changes the reader experience, and the
interactive work never touches the GPU. See [console_spec.md](console_spec.md).

> Status: **Phases 1–2 working.** Day selection, span sizing, the window view
> (clusters + singletons), in-place marking (`same` / `not_same` / `confirm`),
> re-run at new params, and scoring the result against the marks with a
> span-scoped diff vs the production baseline. Embedding-model testing (Phase 3)
> is next. Capture is live on nova. See [SPEC_REVIEW.md](SPEC_REVIEW.md) for
> design notes and the production findings that shaped the schema.

## Architecture

```
cruxwire (public, existing)            bench (this repo, private)
reader app container                   console container
┌──────────────────────┐              ┌───────────────────────────┐
│ ingestion (2h cadence)│             │ web UI: day + span control │
│  [flag] corpus_archive ┼── shared ──►│ engine (CPU, exact XX^T)   │
│   writes JSONL (stdlib)│   volume    │ reads corpus (read-only)   │
└──────────────────────┘   (RO here)  │ owns marks + alt embeddings │
                                       └───────────────────────────┘
                                        GPU re-embed batch (overnight, Nova)
```

- **`schema/`** — the versioned corpus-archive contract, single-sourced here. The
  cruxwire archive-writer conforms to it (see [docs/archive_writer.md](docs/archive_writer.md)).
- **`bench/engine.py`** — exact, CPU-only clustering that faithfully reproduces
  cruxwire's anchored algorithm; the same machinery powers parameter sweeps and
  alternative-model comparisons.
- **`bench/corpus.py`** — reads the JSONL/Parquet archive via DuckDB (lock-free).
- **`bench/web/`** — FastAPI + HTMX console.

## Quick start (no cruxwire needed yet)

```bash
# 1. Install (uv recommended)
uv venv && uv pip install -e ".[dev]"

# 2. Generate a synthetic day so the console has something to show
python scripts/gen_synthetic_corpus.py        # writes ./data/corpus/day=20260624/...

# 3. Run the console
python -m bench                                # http://localhost:8800

# 4. Tests
pytest
```

Or with Docker (Docker Desktop):

```bash
python scripts/gen_synthetic_corpus.py         # populate ./data/corpus first
docker compose up --build                      # http://localhost:8800
```

## Wiring to the real cruxwire

1. Add the archive-writer module to cruxwire per [docs/archive_writer.md](docs/archive_writer.md)
   (single module, one call site in ingestion, behind `BENCH_CAPTURE_ENABLED`).
2. Point both compose files at the same shared volume — cruxwire read-write, the
   bench read-only (`BENCH_CORPUS_DIR`).
3. Enable the flag and let a day accumulate; open it in the console.

## License

A personal, workflow-specific tool, kept source-visible so others can see how
cruxwire's clustering is tuned. No formal license yet (all rights reserved by
default); open an issue if you want to reuse it.
