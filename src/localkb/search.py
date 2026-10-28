"""Hybrid search: vector cosine and CJK-aware FTS5, fused with reciprocal rank."""

from __future__ import annotations

import sqlite3

from localkb.embed import Embedder
from localkb.models import Hit
from localkb.store import Index
from localkb.textutil import fts_query

RRF_K = 60


def hybrid_search(
    index: Index,
    conn: sqlite3.Connection,
    embedder: Embedder,
    query: str,
    k: int,
) -> list[Hit]:
    pool = max(k * 5, 20)
    vector_hits = index.vector_search(conn, embedder.embed_query(query), pool)
    match = fts_query(query)
    keyword_ids = index.keyword_search(conn, match, pool) if match else []
    fused = reciprocal_rank_fusion(
        [chunk_id for chunk_id, _score in vector_hits],
        keyword_ids,
    )
    chosen = fused[:k]
    if not chosen:
        return []
    found = index.hits_by_id(conn, [chunk_id for chunk_id, _score in chosen])
    hits: list[Hit] = []
    for chunk_id, score in chosen:
        hit = found.get(chunk_id)
        if hit is None:
            continue
        hit.score = score
        hits.append(hit)
    return hits


def reciprocal_rank_fusion(*rankings: list[str], k: int = RRF_K) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        seen: set[str] = set()
        for rank, chunk_id in enumerate(ranking):
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))
