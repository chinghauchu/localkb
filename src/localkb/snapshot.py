"""Folder scan and the diff against the stored file image."""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".localkb",
        ".svn",
        ".venv",
        "__pycache__",
        "node_modules",
        "venv",
    }
)
SUPPORTED_SUFFIXES = frozenset({".docx", ".markdown", ".md", ".pdf", ".pptx", ".txt", ".xlsx"})


@dataclass
class ScannedFile:
    rel: str
    size: int
    mtime_ns: int
    sha256: str = ""


@dataclass
class Plan:
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    renamed: list[tuple[str, str]] = field(default_factory=list)
    touched: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


def atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def scan_folder(folder: Path) -> dict[str, ScannedFile]:
    """Stat supported documents. Hidden directories, `.localkb`, and symlinks are skipped."""
    found: dict[str, ScannedFile] = {}
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [
            name for name in dirnames if name not in SKIP_DIR_NAMES and not name.startswith(".")
        ]
        for filename in filenames:
            if filename.startswith("."):
                continue
            path = Path(dirpath) / filename
            if path.is_symlink() or not path.is_file():
                continue
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            rel = path.relative_to(folder).as_posix()
            stat = path.stat()
            found[rel] = ScannedFile(rel=rel, size=stat.st_size, mtime_ns=stat.st_mtime_ns)
    return found


def is_unchanged(manifest: dict[str, dict[str, object]], scanned: dict[str, ScannedFile]) -> bool:
    """True when every path, size, and mtime matches. Callers can skip hashing."""
    if len(manifest) != len(scanned):
        return False
    for rel, item in scanned.items():
        prev = manifest.get(rel)
        if prev is None:
            return False
        if prev["size"] != item.size or prev["mtime_ns"] != item.mtime_ns:
            return False
    return True


def plan_changes(old: dict[str, dict[str, object]], scanned: dict[str, ScannedFile]) -> Plan:
    """Diff a manifest against a hashed scan.

    A rename is a 1:1 pair: one disappeared path and one new path with the same
    content hash. Copies and ambiguous duplicates are added and deleted instead,
    so embeddings are not attached to the wrong file.
    """
    old_paths = set(old)
    new_paths = set(scanned)
    added = sorted(new_paths - old_paths)
    deleted = sorted(old_paths - new_paths)
    modified: list[str] = []
    touched: list[str] = []
    unchanged: list[str] = []

    for rel in sorted(old_paths & new_paths):
        prev = old[rel]
        cur = scanned[rel]
        if cur.size == prev["size"] and cur.mtime_ns == prev["mtime_ns"]:
            unchanged.append(rel)
        elif cur.sha256 == prev["sha256"]:
            touched.append(rel)
        else:
            modified.append(rel)

    deleted_by_hash: dict[str, list[str]] = defaultdict(list)
    for rel in deleted:
        deleted_by_hash[str(old[rel]["sha256"])].append(rel)
    added_by_hash: dict[str, list[str]] = defaultdict(list)
    for rel in added:
        added_by_hash[scanned[rel].sha256].append(rel)

    renamed: list[tuple[str, str]] = []
    consumed_deleted: set[str] = set()
    consumed_added: set[str] = set()
    for digest, old_rels in deleted_by_hash.items():
        new_rels = added_by_hash.get(digest, [])
        if len(old_rels) == 1 and len(new_rels) == 1:
            renamed.append((old_rels[0], new_rels[0]))
            consumed_deleted.add(old_rels[0])
            consumed_added.add(new_rels[0])

    renamed.sort()
    return Plan(
        added=[rel for rel in added if rel not in consumed_added],
        modified=modified,
        deleted=[rel for rel in deleted if rel not in consumed_deleted],
        renamed=renamed,
        touched=touched,
        unchanged=unchanged,
    )
