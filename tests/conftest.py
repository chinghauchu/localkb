from __future__ import annotations

from pathlib import Path

import pytest

from localkb.embed import HashEmbedder
from localkb.pipeline import init_kb
from tests.corpus import write_corpus


@pytest.fixture(autouse=True)
def _reset_hash_counter() -> None:
    HashEmbedder.documents_embedded = 0


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    folder = tmp_path / "docs"
    write_corpus(folder)
    init_kb(folder, embedder="hash")
    return folder
