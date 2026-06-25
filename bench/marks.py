"""Marks store — the operator's private ground truth.

Marks are relationships over article ids, deliberately NOT tied to a cluster id,
span, parameter set, or embedding model, so a mark made under one configuration
stays valid under any other (spec → Window View → Marking, and Data Model → Mark
record). That relational stability is what makes a marked day a fixed evaluation
set for parameter and embedding-model comparison.

Three kinds (spec):
  - ``same``      : these ids should all be in one cluster (a missed merge).
  - ``not_same``  : ids[0] does NOT belong with ids[1:] (an over-merge / wrong
                    member). Stored as "this story vs the ids it was wrongly
                    grouped with", so it stays meaningful as membership shifts.
  - ``confirm``   : these ids form a correct cluster (optional affirmation).

Backed by SQLite on the bench's PRIVATE volume (never the shared corpus volume).
SQLite (stdlib) suits a low-volume, single-operator, mutable store with status
updates better than the columnar corpus reader.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import config

MARK_TYPES = ("same", "not_same", "confirm")
STATUSES = ("new", "reviewed")
CAUSES = ("embedding_miss", "threshold_miss", "sparse_entities", "topic_vs_story", "other")


@dataclass
class Mark:
    mark_id: str
    day: str
    created_at: str
    type: str
    article_ids: list[str]
    note: str | None = None
    status: str = "new"
    cause: str | None = None


def _db_path() -> Path:
    return Path(config.BENCH_DATA_DIR) / "marks.db"


def _connect() -> sqlite3.Connection:
    config.ensure_dirs()
    con = sqlite3.connect(_db_path())
    con.row_factory = sqlite3.Row
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS marks (
            mark_id     TEXT PRIMARY KEY,
            day         TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            type        TEXT NOT NULL,
            article_ids TEXT NOT NULL,   -- JSON array
            note        TEXT,
            status      TEXT NOT NULL DEFAULT 'new',
            cause       TEXT
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_marks_day ON marks(day)")
    return con


def _row_to_mark(r: sqlite3.Row) -> Mark:
    return Mark(
        mark_id=r["mark_id"],
        day=r["day"],
        created_at=r["created_at"],
        type=r["type"],
        article_ids=json.loads(r["article_ids"]),
        note=r["note"],
        status=r["status"],
        cause=r["cause"],
    )


def add_mark(day: str, type: str, article_ids: list[str], note: str | None = None) -> Mark:
    """Create a mark. Order matters for ``not_same``: ids[0] is the story that
    does not belong, ids[1:] are the ids it was wrongly grouped with."""
    if type not in MARK_TYPES:
        raise ValueError(f"unknown mark type: {type}")
    ids = [str(a) for a in dict.fromkeys(article_ids) if a]  # dedup, keep order
    if type in ("same", "confirm") and len(ids) < 2:
        raise ValueError(f"{type} mark needs at least 2 article ids")
    if type == "not_same" and len(ids) < 2:
        raise ValueError("not_same mark needs the odd story plus at least one other id")
    m = Mark(
        mark_id=uuid.uuid4().hex[:12],
        day=day,
        created_at=datetime.now(timezone.utc).isoformat(),
        type=type,
        article_ids=ids,
        note=(note or None),
    )
    with _connect() as con:
        con.execute(
            "INSERT INTO marks (mark_id, day, created_at, type, article_ids, note, status, cause) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (m.mark_id, m.day, m.created_at, m.type, json.dumps(m.article_ids), m.note, m.status, m.cause),
        )
    return m


def list_marks(day: str) -> list[Mark]:
    with _connect() as con:
        rows = con.execute(
            "SELECT * FROM marks WHERE day=? ORDER BY created_at", (day,)
        ).fetchall()
    return [_row_to_mark(r) for r in rows]


def delete_mark(mark_id: str) -> None:
    with _connect() as con:
        con.execute("DELETE FROM marks WHERE mark_id=?", (mark_id,))


def review_mark(mark_id: str, status: str | None = None, cause: str | None = None,
                note: str | None = None) -> None:
    """Set review fields (P1-1). Validates enums; ignores None args."""
    if status is not None and status not in STATUSES:
        raise ValueError(f"unknown status: {status}")
    if cause is not None and cause not in CAUSES:
        raise ValueError(f"unknown cause: {cause}")
    sets, vals = [], []
    for col, val in (("status", status), ("cause", cause), ("note", note)):
        if val is not None:
            sets.append(f"{col}=?")
            vals.append(val)
    if not sets:
        return
    vals.append(mark_id)
    with _connect() as con:
        con.execute(f"UPDATE marks SET {', '.join(sets)} WHERE mark_id=?", vals)
