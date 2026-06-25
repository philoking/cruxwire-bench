#!/usr/bin/env python3
"""Generate a synthetic corpus day so the console runs before cruxwire is wired up.

Writes JSONL parts in the exact shared-volume layout the cruxwire archive-writer
will use (corpus/day=YYYYMMDD/block=HHMM/part-*.jsonl), conforming to the bench's
versioned schema. Embeddings are random unit vectors nudged into a few topic
"centroids" so real clusters and singletons appear.

Deterministic (fixed seed) so runs are reproducible. Usage:
    python scripts/gen_synthetic_corpus.py [--day 20260624] [--out ./data/corpus]
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from schema import PROD_EMBEDDING_DIM, PROD_EMBEDDING_MODEL, SCHEMA_VERSION  # noqa: E402

BLOCKS = ["0000", "0200", "0400", "0600", "0800", "1000", "1200",
          "1400", "1600", "1800", "2000", "2200"]
SOURCES = ["Reuters", "AP", "BBC", "The Verge", "Ars Technica", "Bloomberg", "Wired", "TechCrunch"]
TOPICS = [
    "central bank rate decision", "AI model release", "election results",
    "spacecraft launch", "earthquake relief", "chipmaker earnings",
    "football transfer", "data breach disclosure",
]


def make_day(day: str, out_dir: Path, seed: int = 7) -> int:
    rng = np.random.default_rng(seed)
    pyr = random.Random(seed)
    # One centroid per topic; stories near a centroid should cluster.
    centroids = {t: _unit(rng.normal(size=PROD_EMBEDDING_DIM)) for t in TOPICS}

    written = 0
    for bi, block in enumerate(BLOCKS):
        # Accumulate more stories as the day goes on (mirrors carry-forward).
        n = 6 + bi
        records = []
        for k in range(n):
            topic = pyr.choice(TOPICS)
            # ~25% are loners (no near neighbour) -> singletons.
            loner = pyr.random() < 0.25
            base = _unit(rng.normal(size=PROD_EMBEDDING_DIM)) if loner else centroids[topic]
            vec = _unit(base + rng.normal(scale=0.18 if not loner else 0.6, size=PROD_EMBEDDING_DIM))
            aid = f"{day}-{block}-{k:02d}"
            records.append({
                "schema_version": SCHEMA_VERSION,
                "article_id": aid,
                "day": f"{day[:4]}-{day[4:6]}-{day[6:]}",
                "block_id": block,
                "source": pyr.choice(SOURCES),
                "title": f"{topic.title()} — update {bi}.{k}",
                "url": f"https://example.com/{aid}",
                "published_at": f"{day[:4]}-{day[4:6]}-{day[6:]}T{block[:2]}:{block[2:]}:00Z",
                "ingested_at": f"{day[:4]}-{day[4:6]}-{day[6:]}T{block[:2]}:{block[2:]}:05Z",
                "score": round(pyr.uniform(2.0, 9.5), 1),
                "has_image": pyr.random() < 0.5,
                "body_text": f"Synthetic body about {topic}. " * 8,
                "embedding": [round(float(x), 6) for x in vec],
                "embedding_model": PROD_EMBEDDING_MODEL,
                "embedding_model_version": "synthetic-1",
                "entities": pyr.sample(["Fed", "NASA", "EU", "OpenAI", "Nvidia", "FIFA"], k=2),
                "prod_cluster_id": None,   # left null for synthetic; baseline check no-ops
                "prod_params": {"sim_threshold": 0.82},
            })
        part = out_dir / f"day={day}" / f"block={block}" / "part-0000.jsonl"
        part.parent.mkdir(parents=True, exist_ok=True)
        with part.open("w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")
        written += len(records)
    return written


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n else v


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="20260624")
    ap.add_argument("--out", default="./data/corpus")
    args = ap.parse_args()
    out = Path(args.out).resolve()
    n = make_day(args.day, out)
    print(f"Wrote {n} synthetic articles for {args.day} under {out}")


if __name__ == "__main__":
    main()
