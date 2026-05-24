"""이디야 도메인 룰 — 디카페인/사이즈 등 카페 고유 매핑.

여기서 결정한 룰은 모델에게 시키지 않는다. notes로 모델에 통보만.
"""
from __future__ import annotations

from typing import Optional, Tuple

from app.domain.menu import all_menu_names


_DECAF_MAPPING = {
    # 사용자 발화 키워드 → 정식 메뉴
    "아메리카노": "디카페인콜드브루아메리카노",
    "카페라떼": "디카페인콜드브루라떼",
    "라떼": "디카페인콜드브루라떼",
}


def apply_decaf_rule(text: str) -> Optional[Tuple[str, str]]:
    """디카페인 관련 발화 처리.

    Returns:
        None — 디카페인 발화 아님
        (resolved_menu, note) — 디카페인 콜드브루 메뉴로 매핑됨
        ("REJECT_HOT_DECAF", note) — "디카페인 핫" 같은 불가능 요청
    """
    if "디카페인" not in text:
        return None

    # 디카페인 + 핫 동시 → 거절 안내
    if "핫" in text or "따뜻" in text or "뜨거" in text:
        return (
            "REJECT_HOT_DECAF",
            "이디야는 디카페인이 콜드브루로만 가능해요. 디카페인 콜드브루로 안내.",
        )

    # 발화에서 메뉴 종류 추출 + 디카페인 콜드브루 메뉴로 매핑
    for keyword, menu in _DECAF_MAPPING.items():
        if keyword in text and menu in all_menu_names():
            return (
                menu,
                f"'디카페인 {keyword}' → {menu}로 매핑 (이디야는 디카페인 콜드브루만 운영).",
            )

    return None
