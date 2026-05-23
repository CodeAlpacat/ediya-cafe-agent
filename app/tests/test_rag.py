"""rag.py — 임베딩 메뉴 검색 단위 테스트.

_menu_to_descriptor 같은 순수 함수는 항상 실행된다.
실제 임베딩 검색(MenuRetriever.search, answer_menu_inquiry)은 sentence-transformers와
모델 다운로드가 필요하므로, 미설치 환경에서는 skip 한다.
"""
import pytest

from app.domain.menu import get_category_descriptor, load_menu
from app.llm.rag import (
    RELEVANCE_THRESHOLD,
    _menu_to_descriptor,
    answer_menu_inquiry,
    search_menus,
)


def _sentence_transformers_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401

        return True
    except ImportError:
        return False


requires_embeddings = pytest.mark.skipif(
    not _sentence_transformers_available(),
    reason="sentence-transformers 미설치 — 임베딩 검색 테스트 skip",
)


def _menu(kr: str) -> dict:
    return next(m for m in load_menu()["menus"] if m["kr"] == kr)


# ---------- 순수 함수: descriptor 생성 ----------

def test_descriptor_includes_menu_name():
    assert "아이스아메리카노" in _menu_to_descriptor(_menu("아이스아메리카노"))


def test_descriptor_pulls_category_from_yaml():
    """카테고리 설명이 하드코딩이 아니라 menu_data.yaml에서 와야 한다."""
    item = _menu("핫카페라떼")
    cat_desc = get_category_descriptor(item["category"])
    assert cat_desc
    assert cat_desc in _menu_to_descriptor(item)


def test_descriptor_sweet_tag_reflected():
    """tags:["달콤한"] 메뉴는 descriptor에 태그가 반영되고, 없는 메뉴는 미반영."""
    assert "달콤한" in _menu_to_descriptor(_menu("아이스바닐라라떼"))
    assert "달콤한" not in _menu_to_descriptor(_menu("아이스아메리카노"))


def test_descriptor_temperature_derived_from_name():
    assert "차가운" in _menu_to_descriptor(_menu("아이스아메리카노"))
    assert "따뜻한" in _menu_to_descriptor(_menu("핫아메리카노"))


def test_descriptor_decaf_flag():
    assert "카페인 없는" in _menu_to_descriptor(_menu("디카페인콜드브루아메리카노"))


def test_relevance_threshold_sane():
    assert 0.0 < RELEVANCE_THRESHOLD < 1.0


# ---------- 임베딩 검색 (sentence-transformers + 모델 필요) ----------

@requires_embeddings
def test_search_returns_topk_with_scores():
    hits = search_menus("아메리카노", k=5)
    assert len(hits) == 5
    for h in hits:
        assert "menu" in h and "score" in h
        assert -1.0 <= h["score"] <= 1.0
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)  # 내림차순 정렬


@requires_embeddings
def test_search_semantic_sweet_query():
    """'달콤한 거 추천' 의미 쿼리 → 달콤한 태그 메뉴가 상위권에."""
    hits = search_menus("달콤한 거 추천", k=10)
    sweet = {m["kr"] for m in load_menu()["menus"] if "달콤한" in (m.get("tags") or [])}
    top5 = [h["menu"]["kr"] for h in hits[:5]]
    assert any(name in sweet for name in top5)


@requires_embeddings
def test_answer_menu_inquiry_structure():
    result = answer_menu_inquiry("아메리카노 얼마예요", k=5)
    assert result["answer_basis"] == "rag_search"
    assert result["candidates"]
    assert set(result["candidates"][0].keys()) == {"kr", "category", "price_l", "score"}
