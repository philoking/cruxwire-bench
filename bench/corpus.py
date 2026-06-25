"""Corpus reader: query the shared archive (JSONL or Parquet) via DuckDB.

cruxwire writes JSONL (pure stdlib). The bench reads it directly with DuckDB —
appending a JSONL/Parquet file takes no lock, so the always-on app writer and the
always-on console reader never block each other (spec → Storage and Concurrency,
lock-free approach).

Partition layout on the shared volume:
    corpus/day=YYYYMMDD/block=HHMM/part-*.jsonl   (or part-*.parquet)

Records also carry `day` and `block_id` inline, so selection works regardless of
how strictly the directory layout is followed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from schema import SCHEMA_VERSION
from . import config


@dataclass
class Block:
    block_id: str
    story_count: int


@dataclass
class SchemaWarning:
    seen_version: int
    handled_version: int


_SELECT_COLS = [
    "article_id", "day", "block_id", "source", "title", "summary", "url",
    "published_at", "ingested_at", "score", "has_image", "body_text",
    "embedding", "embedding_model", "embedding_model_version", "entities",
    "prod_cluster_id", "prod_params", "schema_version",
]


def _glob(corpus_dir: Path) -> str:
    """A DuckDB-friendly glob matching both jsonl and parquet parts."""
    return str(corpus_dir / "**" / "part-*")


def _read_relation(con: duckdb.DuckDBPyConnection, corpus_dir: Path):
    """A DuckDB relation over the corpus, selecting only known columns.

    Uses union_by_name so additive schema changes (new columns we don't list)
    are simply ignored, and missing-here columns come back NULL.
    """
    jsonl = list(corpus_dir.rglob("part-*.jsonl"))
    parquet = list(corpus_dir.rglob("part-*.parquet"))
    if not jsonl and not parquet:
        return None
    # Prefer a single homogeneous reader; if both exist, read each and union.
    rels = []
    if parquet:
        rels.append(f"SELECT * FROM read_parquet('{_posix(corpus_dir)}/**/part-*.parquet', union_by_name=true)")
    if jsonl:
        rels.append(f"SELECT * FROM read_json_auto('{_posix(corpus_dir)}/**/part-*.jsonl', union_by_name=true)")
    sql = " UNION ALL BY NAME ".join(rels)
    return con.sql(sql)


def _posix(p: Path) -> str:
    return p.as_posix()


class Corpus:
    """Read-only handle over the shared corpus archive."""

    def __init__(self, corpus_dir: Path | None = None):
        self.corpus_dir = Path(corpus_dir or config.CORPUS_DIR)
        self.con = duckdb.connect(database=":memory:")
        self._warning: SchemaWarning | None = None

    # ── discovery ────────────────────────────────────────────────────────
    def days(self) -> list[str]:
        rel = _read_relation(self.con, self.corpus_dir)
        if rel is None:
            return []
        rel.create_view("corpus", replace=True)
        rows = self.con.sql(
            "SELECT DISTINCT CAST(day AS VARCHAR) AS d FROM corpus ORDER BY d DESC"
        ).fetchall()
        return [r[0] for r in rows]

    def blocks(self, day: str) -> list[Block]:
        rel = _read_relation(self.con, self.corpus_dir)
        if rel is None:
            return []
        rel.create_view("corpus", replace=True)
        rows = self.con.execute(
            "SELECT block_id, COUNT(*) FROM corpus WHERE CAST(day AS VARCHAR)=? "
            "GROUP BY block_id ORDER BY block_id",
            [day],
        ).fetchall()
        return [Block(block_id=str(b), story_count=int(c)) for b, c in rows]

    # ── span loading ─────────────────────────────────────────────────────
    def load_span(self, day: str, start_block: str, end_block: str) -> list[dict]:
        """The deduplicated article set for blocks [start_block, end_block] on `day`.

        Because cruxwire carries unread stories forward, the SAME article_id is
        re-archived in every run it survives — so a multi-block span contains
        duplicate ids. We keep ONE record per article_id (the latest block's, by
        ingested_at), since that block holds the freshest score/cluster context.

        Note the consequence for fidelity (see SPEC_REVIEW.md): a single-block
        span is an exact reproduction of that run; a multi-block span is the
        deduped *union* across the day — a useful experiment, but not a pool any
        single production run actually clustered (retention prunes between runs).

        Returns plain dicts the engine consumes; `embedding` is a list or None.
        """
        rel = _read_relation(self.con, self.corpus_dir)
        if rel is None:
            return []
        rel.create_view("corpus", replace=True)
        cols = ", ".join(_SELECT_COLS)
        cur = self.con.execute(
            f"SELECT {cols} FROM corpus "
            "WHERE CAST(day AS VARCHAR)=? AND block_id >= ? AND block_id <= ? "
            "ORDER BY ingested_at NULLS LAST, block_id, article_id",
            [day, start_block, end_block],
        )
        names = [d[0] for d in cur.description]
        records = [dict(zip(names, row)) for row in cur.fetchall()]

        # Dedup by article_id, keeping the latest occurrence (rows are ascending).
        by_id: dict[str, dict] = {}
        max_seen = 0
        for r in records:
            max_seen = max(max_seen, int(r.get("schema_version") or 0))
            by_id[r.get("article_id")] = r
        if max_seen > SCHEMA_VERSION:
            self._warning = SchemaWarning(seen_version=max_seen, handled_version=SCHEMA_VERSION)
        return [self._to_engine_dict(r) for r in by_id.values()]

    @staticmethod
    def _to_engine_dict(r: dict) -> dict:
        emb = r.get("embedding")
        return {
            "id": r.get("article_id"),
            "title": r.get("title"),
            "summary": r.get("summary"),
            "source": r.get("source"),
            "url": r.get("url"),
            "score": r.get("score") or 0.0,
            "has_image": bool(r.get("has_image")),
            "published_at": r.get("published_at"),
            "ingested_at": r.get("ingested_at"),
            "block_id": r.get("block_id"),
            "embedding": list(emb) if emb is not None else None,
            "entities": list(r.get("entities") or []),
            "prod_cluster_id": r.get("prod_cluster_id"),
        }

    @property
    def schema_warning(self) -> SchemaWarning | None:
        return self._warning
