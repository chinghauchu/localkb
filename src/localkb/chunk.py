"""Paragraph-aware chunking. Location labels stay with the caller."""

from __future__ import annotations

from localkb.textutil import normalize_text, split_window

DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 120


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
