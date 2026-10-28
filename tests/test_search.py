from pathlib import Path

from localkb.models import Hit
from localkb.pipeline import search_kb
from localkb.prompt import build_prompt


def _top(folder: Path, query: str) -> Hit:
    hits = search_kb(folder, query, k=5)
    assert hits, query
    return hits[0]


def test_search_citations_for_each_format(corpus: Path) -> None:
    english = _top(corpus, "quarterly revenue 42 million")
    assert english.path == "reports/quarter.pdf"
    assert english.locator_type == "page"
    assert english.locator == "1"
    assert english.location == "page 1"

    chinese = _top(corpus, "本季營收")
    assert chinese.path == "reports/quarter.pdf"
    assert chinese.locator == "2"
    assert "四千兩百萬" in chinese.text

    sheet = _top(corpus, "本機儲存")
    assert sheet.path == "notes/budget.xlsx"
    assert sheet.locator_type == "sheet"
    assert sheet.locator == "預算"

    slide = _top(corpus, "快照")
    assert slide.path == "notes/overview.pptx"
    assert slide.locator_type == "slide"
    assert slide.locator == "1"

    word = _top(corpus, "March")
    assert word.path == "notes/launch.docx"
    assert word.locator == "Launch plan"

    markdown = _top(corpus, "zephyr-citation-token")
    assert markdown.path == "notes/architecture.md"
    assert markdown.location == "Storage layout"
    assert markdown.score > 0


def test_hidden_paths_are_not_searchable(corpus: Path) -> None:
    for query in ("hidden-dir-token-should-miss", "git-dir-token-should-miss"):
        hits = search_kb(corpus, query, k=5)
        assert all(query not in hit.text for hit in hits)


def test_prompt_names_file_and_location(corpus: Path) -> None:
    hits = search_kb(corpus, "本季營收", k=2)
    prompt = build_prompt("本季營收是多少？", hits)
    assert "reports/quarter.pdf" in prompt
    assert "page 2" in prompt
    assert "Cite every claim" in prompt
    assert "[1]" in prompt
