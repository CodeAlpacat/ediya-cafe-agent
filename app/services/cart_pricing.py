"""카트 항목에 단가/소계 부착 + 총액 계산.

가격 모델:
    line_total = (base_price + Σ option_price_delta) × quantity
즉 옵션(엑스트라/샷추가/시럽 등)이 단가에 가산된다. 옵션 사전에 없는 옵션이
들어와도(예: 모델이 만들어낸 이상한 이름) 0원으로 처리해 응답을 깨지 않는다.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from app.domain.menu import get_menu_price, get_option_price_delta


def enrich_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """각 항목에 단가(`price`), 옵션 합계(`option_total`), 소계(`line_total`) 부착.

    base_price: 메뉴 자체의 라지 가격
    option_total: 옵션 가격 가산 합 (1잔당)
    price: 1잔 가격 = base_price + option_total
    line_total: price × quantity
    """
    out: List[Dict[str, Any]] = []
    for it in items:
        base_price = get_menu_price(it["menu"]) or 0
        option_total = sum(get_option_price_delta(o) for o in (it.get("options") or []))
        unit_price = base_price + option_total
        line_total = unit_price * it["quantity"]
        out.append({
            **it,
            "base_price": base_price,
            "option_total": option_total,
            "price": unit_price,
            "line_total": line_total,
        })
    return out


def enrich_with_total(items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """enrich_items + 총합 한 번에. 라우터 한 줄로 쓰라고 만든 편의."""
    enriched = enrich_items(items)
    total = sum(it["line_total"] for it in enriched)
    return enriched, total
