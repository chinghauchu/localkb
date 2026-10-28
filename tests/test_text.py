from localkb.chunk import chunk_text
from localkb.embed import create_embedder
from localkb.errors import LocalKBError
from localkb.search import reciprocal_rank_fusion
from localkb.textutil import cjk_index_text, fts_query, split_window


def test_cjk_bigrams_leave_english_words() -> None:
    indexed = cjk_index_text("知識庫 quarterly revenue")
    assert "知識" in indexed
    assert "識庫" in indexed
    assert "revenue" in indexed


def test_mixed_script_runs_are_padded_and_kana_hangul_split() -> None:
    mixed = cjk_index_text("Q3營收")
    assert "Q3" in mixed
    assert " 營收 " in mixed
    assert "カタ" in cjk_index_text("カタカナ")
    assert "タカ" in cjk_index_text("カタカナ")
    assert "한글" in cjk_index_text("한글검색")


def test_fts_query_ors_content_words() -> None:
    query = fts_query("the quarterly revenue 本季營收")
    assert query is not None
    assert '"the"' not in query
    assert '"quarterly"' in query
    assert '"revenue"' in query
    assert '"本季"' in query
    assert " OR " in query


def test_split_window_finishes_on_unbroken_cjk() -> None:
    parts = split_window("字" * 5000, 800, 120)
    assert parts
    assert parts[0] == "字" * 800
    assert parts[-1].endswith("字")
    assert len(parts) < 20


def test_chunk_text_keeps_short_paragraphs_together() -> None:
    chunks = chunk_text("alpha\n\nbeta")
    assert chunks == ["alpha\n\nbeta"]


def test_rrf_prefers_documents_in_both_lists() -> None:
    fused = reciprocal_rank_fusion(["a", "b"], ["b", "c"])
    assert fused[0][0] == "b"


def test_ollama_embedder_is_a_stub() -> None:
    try:
        create_embedder("ollama")
    except LocalKBError as exc:
        assert "not implemented" in str(exc)
    else:
        raise AssertionError("expected LocalKBError")
