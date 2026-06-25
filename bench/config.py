"""Bench configuration. Deploy-time wiring via env vars, with local-dev defaults.

The corpus archive lives on the SHARED volume (read-only here). The bench's own
stores (marks, multi-model embeddings) live on the bench's PRIVATE volume — never
the shared one, to keep the cross-repo contract small (spec → Storage and Concurrency).
"""

from __future__ import annotations

import os
from pathlib import Path

# Read-only mount of the shared volume cruxwire writes the corpus archive to.
# Layout: corpus/day=YYYYMMDD/block=HHMM/part-*.jsonl  (app writes JSONL, stdlib only)
#         the bench materializes a typed Parquet/DuckDB view from these.
CORPUS_DIR = Path(os.environ.get("BENCH_CORPUS_DIR", "./data/corpus")).resolve()

# Bench-private volume: marks store, multi-model embeddings, run records, cache.
BENCH_DATA_DIR = Path(os.environ.get("BENCH_DATA_DIR", "./data/bench")).resolve()

# Ollama on Nova, for the overnight GPU re-embed batch (never hit interactively).
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# Web service bind.
HOST = os.environ.get("BENCH_HOST", "0.0.0.0")
PORT = int(os.environ.get("BENCH_PORT", "8800"))


def ensure_dirs() -> None:
    BENCH_DATA_DIR.mkdir(parents=True, exist_ok=True)
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
