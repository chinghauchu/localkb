from localkb.snapshot import ScannedFile, plan_changes


def _file(rel: str, digest: str, size: int = 1, mtime_ns: int = 1) -> ScannedFile:
    return ScannedFile(rel=rel, size=size, mtime_ns=mtime_ns, sha256=digest)


def test_unique_hash_is_a_rename() -> None:
    old = {"a.md": {"size": 1, "mtime_ns": 1, "sha256": "same", "error": None}}
    scanned = {"b.md": _file("b.md", "same")}
    plan = plan_changes(old, scanned)
    assert plan.renamed == [("a.md", "b.md")]
    assert plan.added == []
    assert plan.deleted == []


def test_ambiguous_duplicates_are_not_renames() -> None:
    old = {
        "a.md": {"size": 1, "mtime_ns": 1, "sha256": "same", "error": None},
        "b.md": {"size": 1, "mtime_ns": 1, "sha256": "same", "error": None},
    }
    scanned = {"c.md": _file("c.md", "same")}
    plan = plan_changes(old, scanned)
    assert plan.renamed == []
    assert plan.deleted == ["a.md", "b.md"]
    assert plan.added == ["c.md"]


def test_same_size_and_mtime_is_unchanged_without_reading_hash() -> None:
    old = {"a.md": {"size": 4, "mtime_ns": 9, "sha256": "keep", "error": None}}
    scanned = {"a.md": ScannedFile("a.md", size=4, mtime_ns=9, sha256="")}
    plan = plan_changes(old, scanned)
    assert plan.unchanged == ["a.md"]
    assert plan.modified == []


def test_same_hash_new_mtime_is_metadata_only() -> None:
    old = {"a.md": {"size": 4, "mtime_ns": 9, "sha256": "keep", "error": None}}
    scanned = {"a.md": _file("a.md", "keep", size=4, mtime_ns=10)}
    plan = plan_changes(old, scanned)
    assert plan.touched == ["a.md"]
    assert plan.modified == []
