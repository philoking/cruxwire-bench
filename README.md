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

> Status: **Phase 1 scaffold.** Day selection, span sizing, and the window view
> work against a corpus (real or synthetic). Marking + re-run scoring (Phase 2)
> and embedding-model testing (Phase 3) have clear extension points but are not
> built yet. See [SPEC_REVIEW.md](SPEC_REVIEW.md) for design notes and open items.

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

Private. Not for distribution.
