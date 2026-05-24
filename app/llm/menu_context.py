"""매 턴 LLM에 inject할 메뉴 컨텍스트 빌더.

LLM-first 설계의 핵심: 메뉴 데이터는 코드에 박지 않고 매 턴 system message로
LLM context에 동적 inject한다. 메뉴 변경 = YAML 1줄, 코드 0줄.

전략:
1. 발화 관련 메뉴 후보 (RAG + 키워드 검색) — 5~8개
2. 옵션 카테고리 사전 전체 — 6개 카테고리, 17개 옵션
3. 매장 미보유 항목 명시 (두유/오트밀크 등) — 정직 거절용
"""
from __future__ import annotations

import logging
from typing import List, Optional

from app.cafe_profile import load_profile
from app.domain.menu import (
    get_menu_stock,
    keyword_menu_search,
    load_menu,
)

logger = logging.getLogger("ediya.menu_context")


# 매장 공통 — 손님이 자주 묻지만 우리는 안 다루는 항목.
# 코드에 박지 않고 system msg에 자연어로 알려서 LLM이 정직히 거절.
KNOWN_NON_OFFERINGS = [
    "두유", "오트밀크", "오트 밀크", "오트", "저지방", "락토프리",
    "디카페인 옵션 (디카페인은 별도 메뉴)",
]


def _format_menu_candidate(item: dict) -> str:
    price = item.get("base_price_l", 0)
    stock = get_menu_stock(item["kr"])
    stock_note = ""
    if stock == 0:
        stock_note = " (품절)"
    elif stock is not None and stock > 0:
        stock_note = f" (재고 {stock}개)"
    return f"  - {item['kr']} ({item.get('category', '?')}, {price}원){stock_note}"


def _format_option_catalog() -> str:
    """매장 전체 옵션 사전 — 카테고리별 + 적용 가능 메뉴 종류."""
    data = load_menu()
    cats = data.get("option_categories", [])
    lines = ["[옵션 사전 — 매장에서 제공하는 모든 옵션]"]
    for cat in cats:
        opts = ", ".join(o["kr"] for o in cat["options"])
        applic = cat.get("applicable_categories")
        scope = f"{', '.join(applic)}" if applic else "전체"
        lines.append(f"  - {cat['kr']}: [{opts}]  (적용: {scope})")
    return "\n".join(lines)


def _format_non_offerings() -> str:
    items = ", ".join(KNOWN_NON_OFFERINGS)
    return (
        "[매장에 없는 항목 — 손님이 요청하면 정직히 거절하고 사과]\n"
        f"  {items}"
    )


def _format_slang_aliases() -> str:
    """profile.yaml의 slang_aliases — 카페별 줄임말 사전을 자연어로 LLM에 전달.

    데이터는 YAML 한 곳에. 카페 교체 = profile.yaml 변경 only.
    """
    aliases = (load_profile() or {}).get("slang_aliases") or {}
    if not aliases:
        return ""
    lines = ["[줄임말 사전 — 손님이 이렇게 부르면 다음 메뉴로 해석]"]
    for slang, targets in aliases.items():
        if not targets:
            continue
        if len(targets) == 1:
            lines.append(f"  - '{slang}' → {targets[0]}")
        else:
            lines.append(f"  - '{slang}' → {', '.join(targets)} 중 하나 (모호하면 되묻기)")
    return "\n".join(lines)


def build_menu_context(user_text: str, max_candidates: int = 8) -> str:
    """발화 → 메뉴 컨텍스트 system message.

    LLM이 매 턴 새로 메뉴/옵션/슬랭 사전을 본다. 메뉴 변경 시 YAML 1곳만.
    """
    sections = [
        _build_candidates_section(user_text, max_candidates),
        _format_option_catalog(),
        _format_slang_aliases(),
        _format_non_offerings(),
    ]
    return "\n\n".join(s for s in sections if s)


def _build_candidates_section(user_text: str, max_candidates: int) -> str:
    """발화 관련 메뉴 후보 — 키워드/슬랭 (강 신호) + RAG (의미) 합집합."""
    candidates: List[dict] = []

    # 1) 키워드 매칭 — 정확/축약/슬랭 신호. 가장 강한 매칭이라 1순위.
    #    menu.keyword_menu_search는 슬랭/카테고리 보정까지 내장.
    keyword_hits = keyword_menu_search(user_text, max_k=max_candidates)
    for h in keyword_hits:
        if not any(c.get("kr") == h.get("kr") for c in candidates):
            candidates.append(h)

    # 2) 의미 매칭 (RAG) — 키워드로 못 잡는 새 표현/유사어 보강
    try:
        from app.llm.rag import search_menus

        rag_hits = search_menus(user_text, k=max_candidates)
        rag_threshold = 0.35  # 짧은 발화는 점수 낮으므로 보수적
        for h in rag_hits:
            if h.get("score", 0) < rag_threshold:
                continue
            menu = h.get("menu", {})
            if not any(c.get("kr") == menu.get("kr") for c in candidates):
                candidates.append(menu)
    except Exception as exc:
        logger.debug("RAG 검색 실패: %s", exc)

    # 발화에서 후보 못 찾으면 — fallback으로 카테고리별 대표 1개씩
    if not candidates:
        candidates = _fallback_category_samples()

    candidates = candidates[:max_candidates]

    lines = [
        "[메뉴 후보 — 발화 관련 매장 메뉴 (이게 매장의 단일 진실 소스)]"
    ]
    lines.extend(_format_menu_candidate(c) for c in candidates)
    return "\n".join(lines)


def _fallback_category_samples(per_category: int = 2) -> List[dict]:
    """발화 관련 후보가 0건일 때 — 카테고리별 샘플 메뉴."""
    data = load_menu()
    out: List[dict] = []
    by_cat: dict[str, List[dict]] = {}
    for m in data["menus"]:
        by_cat.setdefault(m.get("category", "etc"), []).append(m)
    for cat, items in by_cat.items():
        out.extend(items[:per_category])
    return out
