"""SQLite index: file image, chunk text, FTS5 keywords, and float32 vectors.

Vector search is exact cosine over the stored matrix. That keeps the knowledge
base in one portable file and avoids a native vector extension. It is the right
trade for a personal library, not for millions of chunks.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from localkb.errors import LocalKBError
from localkb.models import Hit
from localkb.snapshot import atomic_write_json


@dataclass
class NewChunk:
    id: str
    locator_type: str
    locator: str
    text: str
    body: str
    vector: list[float] | None = None


class Index:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.isolation_level = None
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA journal_mode=WAL")
            if write:
                self.ensure_schema(conn)
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            if write:
                conn.execute("COMMIT")
        except Exception:
            if write:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise
        finally:
            conn.close()

    def ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                error TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                locator_type TEXT NOT NULL,
                locator TEXT NOT NULL,
                text TEXT NOT NULL,
                body TEXT NOT NULL,
                vector BLOB NOT NULL,
                ord INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path)")
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    id UNINDEXED,
                    body,
                    tokenize = 'unicode61 remove_diacritics 2'
                )
                """
            )
        except sqlite3.OperationalError as exc:
            raise LocalKBError(
                "this Python build of sqlite3 has no FTS5, which localkb needs for keyword search"
            ) from exc

    def load_manifest(self, conn: sqlite3.Connection) -> dict[str, dict[str, object]]:
        rows = conn.execute("SELECT path, size, mtime_ns, sha256, error FROM files").fetchall()
        return {
            row["path"]: {
                "size": row["size"],
                "mtime_ns": row["mtime_ns"],
                "sha256": row["sha256"],
                "error": row["error"],
            }
            for row in rows
        }

    def counts(self, conn: sqlite3.Connection) -> tuple[int, int]:
        files = int(conn.execute("SELECT COUNT(*) FROM files").fetchone()[0])
        chunks = int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        return files, chunks

    def delete_file(self, conn: sqlite3.Connection, path: str) -> None:
        conn.execute("DELETE FROM chunks WHERE path = ?", (path,))
        conn.execute("DELETE FROM files WHERE path = ?", (path,))

    def rename_file(self, conn: sqlite3.Connection, old: str, new: str) -> None:
        conn.execute("UPDATE chunks SET path = ? WHERE path = ?", (new, old))
        conn.execute("UPDATE files SET path = ? WHERE path = ?", (new, old))

    def touch_file(self, conn: sqlite3.Connection, path: str, size: int, mtime_ns: int) -> None:
        conn.execute(
            "UPDATE files SET size = ?, mtime_ns = ? WHERE path = ?",
            (size, mtime_ns, path),
        )

    def upsert_file(
        self,
        conn: sqlite3.Connection,
        *,
        path: str,
        size: int,
        mtime_ns: int,
        sha256: str,
        error: str | None,
        chunks: list[NewChunk],
    ) -> None:
        conn.execute("DELETE FROM chunks WHERE path = ?", (path,))
        conn.execute(
            """
            INSERT INTO files(path, size, mtime_ns, sha256, error)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                size = excluded.size,
                mtime_ns = excluded.mtime_ns,
                sha256 = excluded.sha256,
                error = excluded.error
            """,
            (path, size, mtime_ns, sha256, error),
        )
        if not chunks:
            return
        conn.executemany(
            """
            INSERT INTO chunks(id, path, locator_type, locator, text, body, vector, ord)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    chunk.id,
                    path,
                    chunk.locator_type,
                    chunk.locator,
                    chunk.text,
                    chunk.body,
                    _pack_vector(chunk.vector),
                    index,
                )
                for index, chunk in enumerate(chunks)
            ],
        )

    def rebuild_fts(self, conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM chunks_fts")
        conn.execute("INSERT INTO chunks_fts(id, body) SELECT id, body FROM chunks")

    def keyword_search(self, conn: sqlite3.Connection, match: str, limit: int) -> list[str]:
        try:
            rows = conn.execute(
                """
                SELECT id FROM chunks_fts
                WHERE chunks_fts MATCH ?
                ORDER BY bm25(chunks_fts)
                LIMIT ?
                """,
                (match, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [str(row["id"]) for row in rows]

    def vector_search(
        self, conn: sqlite3.Connection, query: list[float], limit: int
    ) -> list[tuple[str, float]]:
        rows = conn.execute("SELECT id, vector FROM chunks").fetchall()
        if not rows or limit <= 0:
            return []
        query_vec = np.asarray(query, dtype=np.float32)
        dim = int(query_vec.shape[0])
        ids: list[str] = []
        vectors: list[np.ndarray] = []
        for row in rows:
            raw = row["vector"]
            if len(raw) != dim * 4:
                raise LocalKBError(
                    "stored vectors do not match the embedding model. "
                    "Rebuild with: localkb init --force <folder>"
                )
            ids.append(str(row["id"]))
            vectors.append(np.frombuffer(raw, dtype=np.float32).copy())
        matrix = np.vstack(vectors)
        scores = matrix @ query_vec
        k = min(limit, len(ids))
        if k == len(ids):
            order = np.argsort(-scores)
        else:
            partial = np.argpartition(-scores, k - 1)[:k]
            order = partial[np.argsort(-scores[partial])]
        return [(ids[int(i)], float(scores[int(i)])) for i in order]

    def get_chunk(self, conn: sqlite3.Connection, chunk_id: str) -> Hit | None:
        row = conn.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        if row is None:
            return None
        return _row_to_hit(row, 0.0)

    def hits_by_id(self, conn: sqlite3.Connection, ids: list[str]) -> dict[str, Hit]:
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT * FROM chunks WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        return {str(row["id"]): _row_to_hit(row, 0.0) for row in rows}

    def list_documents(self, conn: sqlite3.Connection) -> list[dict[str, object]]:
        rows = conn.execute(
            """
            SELECT files.path AS path, COUNT(chunks.id) AS chunks
            FROM files
            LEFT JOIN chunks ON chunks.path = files.path
            GROUP BY files.path
            ORDER BY files.path
            """
        ).fetchall()
        return [{"file": row["path"], "chunks": int(row["chunks"])} for row in rows]

    def export_snapshot(self, conn: sqlite3.Connection, path: Path) -> None:
        grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in conn.execute(
            "SELECT id, path, locator_type, locator FROM chunks ORDER BY path, ord, id"
        ):
            grouped[str(row["path"])].append(
                {
                    "id": str(row["id"]),
                    "locator_type": str(row["locator_type"]),
                    "locator": str(row["locator"]),
                }
            )
        files: dict[str, dict[str, object]] = {}
        for row in conn.execute(
            "SELECT path, size, mtime_ns, sha256, error FROM files ORDER BY path"
        ):
            entry: dict[str, object] = {
                "size": int(row["size"]),
                "mtime_ns": int(row["mtime_ns"]),
                "sha256": str(row["sha256"]),
                "chunks": grouped.get(str(row["path"]), []),
            }
            if row["error"]:
                entry["error"] = str(row["error"])
            files[str(row["path"])] = entry
        atomic_write_json(path, {"version": 1, "files": files})


def _pack_vector(vector: list[float] | None) -> bytes:
    if not vector:
        raise LocalKBError("internal error: chunk is missing a vector")
    array = np.ascontiguousarray(vector, dtype=np.float32)
    return array.tobytes()


def _row_to_hit(row: sqlite3.Row, score: float) -> Hit:
    return Hit(
        id=str(row["id"]),
        path=str(row["path"]),
        locator_type=str(row["locator_type"]),
        locator=str(row["locator"]),
        text=str(row["text"]),
        score=score,
    )
