"""Turn a supported file into located text sections."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from localkb.textutil import normalize_text

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_SLIDE = re.compile(r"<!--\s*Slide number:\s*(\d+)\s*-->")
_SHEET = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)

_MARKITDOWN = None


@dataclass(frozen=True)
class Section:
    locator_type: str
    locator: str
    text: str


def extract_document(path: Path) -> list[Section]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix == ".docx":
        return _split_markdown(_markitdown(path), locator_type="section")
    if suffix == ".xlsx":
        return _split_sheets(_markitdown(path))
    if suffix == ".pptx":
        return _split_slides(_markitdown(path))
    if suffix in {".md", ".markdown"}:
        return _split_markdown(_read_text(path), locator_type="section")
    if suffix == ".txt":
        text = _read_text(path)
        return [Section("part", "1", text)] if text else []
    return []


def _read_text(path: Path) -> str:
    return normalize_text(path.read_text(encoding="utf-8", errors="replace"))


def _markitdown(path: Path) -> str:
    global _MARKITDOWN
    if _MARKITDOWN is None:
        from markitdown import MarkItDown

        _MARKITDOWN = MarkItDown()
    result = _MARKITDOWN.convert(str(path))
    return normalize_text(result.text_content or "")


def _extract_pdf(path: Path) -> list[Section]:
    import pymupdf

    document = pymupdf.open(path)
    try:
        sections: list[Section] = []
        for number, page in enumerate(document, start=1):
            text = normalize_text(page.get_text("text") or "")
            if text:
                sections.append(Section("page", str(number), text))
        return sections
    finally:
        document.close()


def _split_markdown(text: str, *, locator_type: str) -> list[Section]:
    text = normalize_text(text)
    if not text:
        return []
    matches = list(_HEADING.finditer(text))
    if not matches:
        return [Section(locator_type, "document", text)]

    sections: list[Section] = []
    preamble = text[: matches[0].start()].strip()
    if preamble:
        sections.append(Section(locator_type, "document", preamble))

    stack: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        level = len(match.group(1))
        title = match.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end].strip()
        content = f"{title}\n{body}".strip() if body else title
        if content:
            sections.append(Section(locator_type, " / ".join(item[1] for item in stack), content))
    return sections


def _split_sheets(text: str) -> list[Section]:
    text = normalize_text(text)
    if not text:
        return []
    parts = _SHEET.split(text)
    if len(parts) < 3:
        return [Section("sheet", "sheet", text)]
    sections: list[Section] = []
    preamble = parts[0].strip()
    if preamble:
        sections.append(Section("sheet", "workbook", preamble))
    names = parts[1::2]
    bodies = parts[2::2]
    for name, body in zip(names, bodies, strict=True):
        sheet = name.strip()
        content = f"{sheet}\n{body.strip()}".strip()
        if content:
            sections.append(Section("sheet", sheet, content))
    return sections


def _split_slides(text: str) -> list[Section]:
    text = normalize_text(text)
    if not text:
        return []
    parts = _SLIDE.split(text)
    if len(parts) < 3:
        return [Section("slide", "1", text)]
    sections: list[Section] = []
    numbers = parts[1::2]
    bodies = parts[2::2]
    for number, body in zip(numbers, bodies, strict=True):
        content = body.strip()
        if content:
            sections.append(Section("slide", str(number), content))
    return sections
