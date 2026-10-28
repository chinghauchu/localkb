"""Paragraph-aware chunking. Location labels stay with the caller."""

from __future__ import annotations

import hashlib

from localkb.textutil import normalize_text, split_window

DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 120


def chunk_id(
    path: str,
    file_sha: str,
    locator_type: str,
    locator: str,
    ordinal: int,
    text: str,
) -> str:
    """Stable id that includes the path, so identical files do not share a primary key.

    A rename recomputes the id from the new path and keeps the stored vector.
    """
    raw = "\n".join((path, file_sha, locator_type, locator, str(ordinal), text))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def chunk_text(
    text: str,
    size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    text = normalize_text(text)
    if not text:
        return []
    if len(text) <= size:
        return [text]

    chunks: list[str] = []
    buffer = ""
    for paragraph in (part.strip() for part in text.split("\n\n")):
        if not paragraph:
            continue
        if len(paragraph) > size:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            chunks.extend(split_window(paragraph, size, overlap))
            continue
        candidate = f"{buffer}\n\n{paragraph}" if buffer else paragraph
        if len(candidate) <= size:
            buffer = candidate
            continue
        if buffer:
            chunks.append(buffer)
        buffer = paragraph
    if buffer:
        chunks.append(buffer)
    return chunks
