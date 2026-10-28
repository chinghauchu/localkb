import json
import os
from pathlib import Path

import pytest

from localkb.embed import HashEmbedder
from localkb.errors import LocalKBError
from localkb.pipeline import init_kb, search_kb, update_kb
from localkb.store import Index


def test_init_writes_portable_image(corpus: Path) -> None:
    kb = corpus / ".localkb"
    meta = json.loads((kb / "meta.json").read_text(encoding="utf-8"))
    snapshot = json.loads((kb / "snapshot.json").read_text(encoding="utf-8"))
    assert meta["embedder"] == "hash"
    assert meta["format"] == 1
    assert meta["files"] == 6
    assert meta["chunks"] >= 5
    pdf = snapshot["files"]["reports/quarter.pdf"]
    assert pdf["sha256"]
    assert pdf["size"] > 0
    assert pdf["mtime_ns"] > 0
    pages = {chunk["locator"] for chunk in pdf["chunks"] if chunk["locator_type"] == "page"}
    assert pages == {"1", "2"}
    assert "notes/architecture.md" in snapshot["files"]
    assert ".private/secret.txt" not in snapshot["files"]
    assert ".git/hidden.txt" not in snapshot["files"]


def test_init_twice_requires_force(corpus: Path) -> None:
    with pytest.raises(LocalKBError, match="already exists"):
        init_kb(corpus, embedder="hash")
    report = init_kb(corpus, embedder="hash", force=True)
    assert report.files == 6
    assert search_kb(corpus, "zephyr-citation-token")[0].path == "notes/architecture.md"


def test_update_without_init_fails(tmp_path: Path) -> None:
    with pytest.raises(LocalKBError, match="no knowledge base"):
        update_kb(tmp_path)


def test_noop_update_does_not_hash_or_embed(corpus: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("content hash was computed")

    monkeypatch.setattr("localkb.pipeline.hash_file", boom)
    before = HashEmbedder.documents_embedded
    report = update_kb(corpus)
    assert report.added == []
    assert report.modified == []
    assert report.deleted == []
    assert report.renamed == []
    assert report.unchanged == 6
    assert HashEmbedder.documents_embedded == before
    assert report.elapsed_seconds < 2


def test_update_add_modify_delete_and_rename(corpus: Path) -> None:
    readme = corpus / "readme.txt"
    readme.write_text("The codename is violet-harbor.\n", encoding="utf-8")
    before = HashEmbedder.documents_embedded
    modified = update_kb(corpus)
    assert modified.modified == ["readme.txt"]
    assert HashEmbedder.documents_embedded > before
    assert "violet-harbor" in search_kb(corpus, "violet-harbor")[0].text
    assert all(
        "beside your files" not in hit.text for hit in search_kb(corpus, "beside your files")
    )

    extra = corpus / "notes" / "extra.md"
    extra.write_text("# Extra\n\nThe token amber-lighthouse is new.\n", encoding="utf-8")
    added = update_kb(corpus)
    assert added.added == ["notes/extra.md"]
    assert search_kb(corpus, "amber-lighthouse")[0].path == "notes/extra.md"

    readme.unlink()
    deleted = update_kb(corpus)
    assert deleted.deleted == ["readme.txt"]
    assert all(hit.path != "readme.txt" for hit in search_kb(corpus, "violet-harbor"))

    index = Index(corpus / ".localkb" / "index.sqlite")
    with index.connect() as conn:
        before_rows = conn.execute(
            "SELECT id, vector FROM chunks WHERE path = ?",
            ("notes/architecture.md",),
        ).fetchall()
    before_vectors = {row["id"]: bytes(row["vector"]) for row in before_rows}
    assert before_vectors
    embedded = HashEmbedder.documents_embedded
    (corpus / "notes" / "architecture.md").rename(corpus / "notes" / "design.md")
    renamed = update_kb(corpus)
    assert renamed.renamed == [("notes/architecture.md", "notes/design.md")]
    assert HashEmbedder.documents_embedded == embedded
    with index.connect() as conn:
        after_rows = conn.execute(
            "SELECT id, vector FROM chunks WHERE path = ?",
            ("notes/design.md",),
        ).fetchall()
    assert {row["id"]: bytes(row["vector"]) for row in after_rows} == before_vectors
    hit = search_kb(corpus, "zephyr-citation-token")[0]
    assert hit.path == "notes/design.md"
    assert hit.location == "Storage layout"


def test_mtime_only_change_does_not_embed(corpus: Path) -> None:
    path = corpus / "readme.txt"
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    before = HashEmbedder.documents_embedded
    report = update_kb(corpus)
    assert report.refreshed == ["readme.txt"]
    assert report.modified == []
    assert HashEmbedder.documents_embedded == before
