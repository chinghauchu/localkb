"""Build and refresh the knowledge base stored in `<folder>/.localkb/`."""

from __future__ import annotations

import hashlib
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from localkb.chunk import chunk_text
from localkb.embed import Embedder, create_embedder
from localkb.errors import LocalKBError
from localkb.extract import Section, extract_document
from localkb.models import Hit, Report
from localkb.search import hybrid_search
from localkb.snapshot import (
    ScannedFile,
    atomic_write_json,
    is_unchanged,
    plan_changes,
    scan_folder,
)
from localkb.snapshot import (
    sha256_file as hash_file,
)
from localkb.store import Index, NewChunk
from localkb.textutil import cjk_index_text

KB_DIRNAME = ".localkb"
FORMAT = 1


def init_kb(
    folder: Path,
    *,
    embedder: str = "fastembed",
    model: str | None = None,
    force: bool = False,
) -> Report:
    folder = check_dir(folder)
    # Validate the embedder before replacing an existing knowledge base.
    embedder_impl = create_embedder(embedder, model)
    kb = _kb_dir(folder)
    if kb.exists():
        if not force:
            raise LocalKBError(
                f"{kb} already exists. Use 'localkb update' or 'localkb init --force'."
            )
        if not kb.is_dir():
            raise LocalKBError(f"{kb} exists and is not a directory")
        import shutil

        shutil.rmtree(kb)

    started = time.perf_counter()
    scanned = scan_folder(folder)
    skipped: list[tuple[str, str]] = []
    for rel, item in list(scanned.items()):
        try:
            item.sha256 = hash_file(_safe_join(folder, rel))
        except OSError as exc:
            skipped.append((rel, f"could not read: {exc}"))
            del scanned[rel]
    plan = plan_changes({}, scanned)
    created = _now()
    return _apply(
        folder,
        Index(kb / "index.sqlite"),
        embedder_impl,
        scanned,
        plan,
        started=started,
        created=created,
        skipped=skipped,
    )


def update_kb(folder: Path) -> Report:
    folder = check_dir(folder)
    kb = require_kb(folder)
    meta = load_meta(kb)
    started = time.perf_counter()
    index = Index(kb / "index.sqlite")
    with index.connect() as conn:
        manifest = index.load_manifest(conn)
        files, chunks = index.counts(conn)
    scanned = scan_folder(folder)
    if is_unchanged(manifest, scanned):
        _heal_sidecar(kb, meta, files, chunks, index)
        return Report(
            unchanged=files,
            elapsed_seconds=time.perf_counter() - started,
            files=files,
            chunks=chunks,
        )

    skipped: list[tuple[str, str]] = []
    for rel, item in list(scanned.items()):
        prev = manifest.get(rel)
        if prev and prev["size"] == item.size and prev["mtime_ns"] == item.mtime_ns:
            item.sha256 = str(prev["sha256"])
            continue
        try:
            item.sha256 = hash_file(_safe_join(folder, rel))
        except OSError as exc:
            skipped.append((rel, f"could not read: {exc}"))
            if prev:
                item.size = int(prev["size"])
                item.mtime_ns = int(prev["mtime_ns"])
                item.sha256 = str(prev["sha256"])
            else:
                del scanned[rel]

    plan = plan_changes(manifest, scanned)
    embedder = create_embedder(str(meta["embedder"]), str(meta["model"]))
    return _apply(
        folder,
        index,
        embedder,
        scanned,
        plan,
        started=started,
        created=str(meta["created"]),
        skipped=skipped,
    )


def search_kb(folder: Path, query: str, *, k: int = 5) -> list[Hit]:
    folder = check_dir(folder)
    kb = require_kb(folder)
    query = query.strip()
    if not query:
        raise LocalKBError("query is empty")
    if k < 1 or k > 50:
        raise LocalKBError("k must be between 1 and 50")
    meta = load_meta(kb)
    embedder = create_embedder(str(meta["embedder"]), str(meta["model"]))
    index = Index(kb / "index.sqlite")
    with index.connect() as conn:
        return hybrid_search(index, conn, embedder, query, k)


def fetch_chunk(folder: Path, chunk_id: str) -> Hit:
    folder = check_dir(folder)
    kb = require_kb(folder)
    index = Index(kb / "index.sqlite")
    with index.connect() as conn:
        hit = index.get_chunk(conn, chunk_id)
    if hit is None:
        raise LocalKBError(f"unknown excerpt id {chunk_id}")
    return hit


def list_documents(folder: Path) -> list[dict[str, object]]:
    folder = check_dir(folder)
    kb = require_kb(folder)
    index = Index(kb / "index.sqlite")
    with index.connect() as conn:
        return index.list_documents(conn)


def status_kb(folder: Path) -> dict[str, object]:
    folder = check_dir(folder)
    kb = require_kb(folder)
    meta = load_meta(kb)
    index = Index(kb / "index.sqlite")
    with index.connect() as conn:
        files, chunks = index.counts(conn)
    index_path = kb / "index.sqlite"
    return {
        "folder": str(folder),
        "knowledge_base": str(kb),
        "files": files,
        "chunks": chunks,
        "embedder": meta.get("embedder"),
        "model": meta.get("model"),
        "dim": meta.get("dim"),
        "created": meta.get("created"),
        "updated": meta.get("updated"),
        "index_bytes": index_path.stat().st_size if index_path.exists() else 0,
    }


def format_status(info: dict[str, object]) -> str:
    labels = [
        ("folder", "folder"),
        ("knowledge_base", "knowledge base"),
        ("files", "files"),
        ("chunks", "chunks"),
        ("embedder", "embedder"),
        ("model", "model"),
        ("dim", "dimensions"),
        ("created", "created"),
        ("updated", "updated"),
        ("index_bytes", "index bytes"),
    ]
    width = max(len(label) for _key, label in labels)
    lines = [f"{label.ljust(width)}  {info.get(key)}" for key, label in labels]
    return "\n".join(lines)


def check_dir(folder: Path) -> Path:
    folder = folder.expanduser().resolve()
    if folder.name == KB_DIRNAME and folder.is_dir():
        folder = folder.parent
    if not folder.is_dir():
        raise LocalKBError(f"not a directory: {folder}")
    return folder


def require_kb(folder: Path) -> Path:
    kb = _kb_dir(folder)
    if not (kb / "meta.json").is_file() or not (kb / "index.sqlite").is_file():
        raise LocalKBError(f"no knowledge base in {folder}. Create one with: localkb init {folder}")
    return kb


def load_meta(kb: Path) -> dict[str, object]:
    path = kb / "meta.json"
    if not path.is_file():
        raise LocalKBError(f"{path} is missing. Run localkb init again.")
    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LocalKBError(f"{path} is invalid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise LocalKBError(
            "this knowledge base was written by a different localkb format. "
            "Rebuild with: localkb init --force <folder>"
        )
    for key in ("created", "embedder", "model", "dim"):
        if key not in data:
            raise LocalKBError(f"{path} is missing {key}")
    return data


def _apply(
    folder: Path,
    index: Index,
    embedder: Embedder,
    scanned: dict[str, ScannedFile],
    plan,
    *,
    started: float,
    created: str,
    skipped: list[tuple[str, str]],
) -> Report:
    prepared: dict[str, tuple[ScannedFile, list[NewChunk], str | None]] = {}
    pending: list[NewChunk] = []
    for rel in [*plan.added, *plan.modified]:
        item = scanned[rel]
        try:
            sections = extract_document(_safe_join(folder, rel))
        except Exception as exc:
            message = _short_error(exc)
            skipped.append((rel, message))
            prepared[rel] = (item, [], message)
            continue
        chunks = _sections_to_chunks(item.sha256, sections)
        if not chunks:
            skipped.append((rel, "no text extracted"))
        prepared[rel] = (item, chunks, None)
        pending.extend(chunks)

    if pending:
        if embedder.name == "fastembed":
            print(
                f"embedding {len(pending)} chunks with {embedder.model}...",
                file=sys.stderr,
            )
        vectors = embedder.embed_documents([chunk.text for chunk in pending])
        if len(vectors) != len(pending):
            raise LocalKBError("embedder returned an unexpected number of vectors")
        for chunk, vector in zip(pending, vectors, strict=True):
            if len(vector) != embedder.dim:
                raise LocalKBError(
                    "embedding dimension "
                    f"{len(vector)} does not match {embedder.model} ({embedder.dim})"
                )
            chunk.vector = vector

    kb = _kb_dir(folder)
    with index.connect(write=True) as conn:
        for rel in plan.deleted:
            index.delete_file(conn, rel)
        for old, new in plan.renamed:
            index.rename_file(conn, old, new)
            item = scanned[new]
            index.touch_file(conn, new, item.size, item.mtime_ns)
        for rel in plan.touched:
            item = scanned[rel]
            index.touch_file(conn, rel, item.size, item.mtime_ns)
        for rel, (item, chunks, error) in prepared.items():
            index.upsert_file(
                conn,
                path=rel,
                size=item.size,
                mtime_ns=item.mtime_ns,
                sha256=item.sha256,
                error=error,
                chunks=chunks,
            )
        if plan.deleted or prepared:
            index.rebuild_fts(conn)
        files, chunk_count = index.counts(conn)
        index.export_snapshot(conn, kb / "snapshot.json")
    _write_meta(
        kb,
        created=created,
        embedder=embedder,
        files=files,
        chunks=chunk_count,
    )
    return Report(
        added=list(plan.added),
        modified=list(plan.modified),
        deleted=list(plan.deleted),
        renamed=list(plan.renamed),
        refreshed=list(plan.touched),
        unchanged=len(plan.unchanged),
        skipped=skipped,
        elapsed_seconds=time.perf_counter() - started,
        files=files,
        chunks=chunk_count,
    )


def _sections_to_chunks(file_sha: str, sections: list[Section]) -> list[NewChunk]:
    chunks: list[NewChunk] = []
    ordinal = 0
    for section in sections:
        pieces = chunk_text(section.text)
        for offset, piece in enumerate(pieces):
            if section.locator_type == "part" and len(pieces) > 1:
                locator = str(offset + 1)
            else:
                locator = section.locator
            chunks.append(
                NewChunk(
                    id=_chunk_id(file_sha, section.locator_type, locator, ordinal, piece),
                    locator_type=section.locator_type,
                    locator=locator,
                    text=piece,
                    body=cjk_index_text(piece),
                )
            )
            ordinal += 1
    return chunks


def _chunk_id(file_sha: str, locator_type: str, locator: str, ordinal: int, text: str) -> str:
    raw = f"{file_sha}\n{locator_type}\n{locator}\n{ordinal}\n{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _write_meta(kb: Path, *, created: str, embedder: Embedder, files: int, chunks: int) -> None:
    _write_meta_values(
        kb,
        created=created,
        embedder=embedder.name,
        model=embedder.model,
        dim=embedder.dim,
        files=files,
        chunks=chunks,
    )


def _write_meta_values(
    kb: Path,
    *,
    created: str,
    embedder: str,
    model: str,
    dim: int,
    files: int,
    chunks: int,
) -> None:
    atomic_write_json(
        kb / "meta.json",
        {
            "format": FORMAT,
            "created": created,
            "updated": _now(),
            "embedder": embedder,
            "model": model,
            "dim": dim,
            "files": files,
            "chunks": chunks,
        },
    )


def _heal_sidecar(
    kb: Path,
    meta: dict[str, object],
    files: int,
    chunks: int,
    index: Index,
) -> None:
    """Rewrite the human-readable image if a previous run committed the index and then stopped."""
    snapshot_ok = (kb / "snapshot.json").is_file()
    counts_ok = meta.get("files") == files and meta.get("chunks") == chunks
    if snapshot_ok and counts_ok:
        return
    with index.connect() as conn:
        index.export_snapshot(conn, kb / "snapshot.json")
    _write_meta_values(
        kb,
        created=str(meta["created"]),
        embedder=str(meta["embedder"]),
        model=str(meta["model"]),
        dim=int(meta["dim"]),
        files=files,
        chunks=chunks,
    )


def _kb_dir(folder: Path) -> Path:
    return folder / KB_DIRNAME


def _safe_join(root: Path, rel: str) -> Path:
    path = (root / rel).resolve()
    root_resolved = root.resolve()
    if path != root_resolved and root_resolved not in path.parents:
        raise LocalKBError(f"path escapes the folder: {rel}")
    return path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _short_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    if len(text) <= 400:
        return text
    return text[:397] + "..."
