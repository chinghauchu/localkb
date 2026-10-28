"""Text helpers: CJK bigrams, citations, and chunk windows."""

from __future__ import annotations

import re
from urllib.parse import quote

_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_TOKEN = re.compile(r"\w+", flags=re.UNICODE)

# Short English function words drown keyword search. CJK bigrams are not in this set.
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "with",
        "you",
        "your",
    }
)


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def cjk_index_text(text: str) -> str:
    """Insert overlapping bigrams inside CJK runs so FTS can match without spaces.

    「知識庫」 becomes 「知識 識庫」. English and numbers are left intact.
    """

    def repl(match: re.Match[str]) -> str:
        run = match.group(0)
        if len(run) == 1:
            return run
        return " ".join(run[i : i + 2] for i in range(len(run) - 1))

    return _CJK_RUN.sub(repl, text)


def fts_query(text: str, *, limit: int = 32) -> str | None:
    """Build an FTS5 OR query from a natural-language question."""
    tokens: list[str] = []
    seen: set[str] = set()
    for raw in _TOKEN.findall(cjk_index_text(text).lower()):
        token = raw.replace('"', "").strip("_")
        if not token or token in _STOPWORDS or token in seen:
            continue
        if len(token) < 2 and not _CJK_RUN.fullmatch(token):
            continue
        seen.add(token)
        tokens.append(token)
        if len(tokens) >= limit:
            break
    if not tokens:
        return None
    return " OR ".join(f'"{token}"' for token in tokens)


def split_window(text: str, size: int, overlap: int) -> list[str]:
    """Split text into windows. Prefers whitespace breaks when they exist."""
    if size <= 0:
        raise ValueError("size must be positive")
    if overlap < 0 or overlap >= size:
        overlap = max(size // 5, 0)
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    chunks: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + size, length)
        if end < length:
            break_at = text.rfind(" ", start + size // 2, end)
            if break_at != -1:
                end = break_at
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= length:
            break
        next_start = end - overlap
        if next_start <= start:
            next_start = end
        start = next_start
    return chunks


def format_location(locator_type: str, locator: str) -> str:
    if locator_type == "page":
        return f"page {locator}"
    if locator_type == "slide":
        return f"slide {locator}"
    if locator_type == "sheet":
        return f"sheet {locator}"
    if locator_type == "part":
        return f"part {locator}"
    if locator_type == "section":
        return locator
    return f"{locator_type} {locator}".strip()


def locator_fields(locator_type: str, locator: str) -> dict[str, int | str | None]:
    page = int(locator) if locator_type == "page" and locator.isdigit() else None
    slide = int(locator) if locator_type == "slide" and locator.isdigit() else None
    sheet = locator if locator_type == "sheet" else None
    return {"page": page, "slide": slide, "sheet": sheet}


def file_url(root, rel: str, locator_type: str, locator: str) -> str:
    from pathlib import Path

    uri = (Path(root) / rel).resolve().as_uri()
    if locator_type == "page":
        return f"{uri}#page={quote(locator)}"
    if locator_type == "slide":
        return f"{uri}#slide={quote(locator)}"
    if locator_type == "sheet":
        return f"{uri}#sheet={quote(locator)}"
    if locator_type == "section":
        return f"{uri}#section={quote(locator)}"
    if locator_type == "part":
        return f"{uri}#part={quote(locator)}"
    return uri
