"""Shared records for search hits and update reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from localkb.textutil import file_url, format_location, locator_fields


@dataclass
class Hit:
    id: str
    path: str
    locator_type: str
    locator: str
    text: str
    score: float

    @property
    def location(self) -> str:
        return format_location(self.locator_type, self.locator)

    @property
    def title(self) -> str:
        return f"{self.path} ({self.location})"

    def url(self, root: Path) -> str:
        return file_url(root, self.path, self.locator_type, self.locator)

    def as_dict(self, root: Path | None = None) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "file": self.path,
            "locator_type": self.locator_type,
            "locator": self.locator,
            "location": self.location,
            "score": round(self.score, 6),
            "text": self.text,
        }
        payload.update(locator_fields(self.locator_type, self.locator))
        if root is not None:
            payload["title"] = self.title
            payload["url"] = self.url(root)
        return payload


@dataclass
class Report:
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    renamed: list[tuple[str, str]] = field(default_factory=list)
    refreshed: list[str] = field(default_factory=list)
    unchanged: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    files: int = 0
    chunks: int = 0

    def format(self, verb: str) -> str:
        lines = [
            f"{verb} in {self.elapsed_seconds:.2f}s",
            f"  added:     {len(self.added)}",
            f"  modified:  {len(self.modified)}",
            f"  deleted:   {len(self.deleted)}",
            f"  renamed:   {len(self.renamed)}",
            f"  unchanged: {self.unchanged}",
        ]
        if self.refreshed:
            lines.append(f"  refreshed: {len(self.refreshed)} (size or mtime only)")
        for rel in self.added:
            lines.append(f"  + {rel}")
        for rel in self.modified:
            lines.append(f"  ~ {rel}")
        for rel in self.deleted:
            lines.append(f"  - {rel}")
        for old, new in self.renamed:
            lines.append(f"  {old} -> {new}")
        for rel, reason in self.skipped:
            lines.append(f"  ! {rel}: {reason}")
        lines.append(f"  files: {self.files}  chunks: {self.chunks}")
        return "\n".join(lines)
