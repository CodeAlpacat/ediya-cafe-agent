"""메뉴/옵션 사전 로더 + 검증 헬퍼."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

MENU_DATA_PATH = Path(__file__).resolve().parent / "menu_data.yaml"


@lru_cache(maxsize=1)
def load_menu() -> Dict[str, Any]:
    with MENU_DATA_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _menu_index() -> Dict[str, Dict[str, Any]]:
    return {m["kr"]: m for m in load_menu()["menus"]}


def _option_categories() -> List[Dict[str, Any]]:
    return load_menu()["option_categories"]


def is_valid_menu(menu_kr: str) -> bool:
    if not menu_kr:
        return False
    return menu_kr in _menu_index()


def is_valid_option_in_category(option_kr: str, category_kr: str) -> bool:
    for cat in _option_categories():
        if cat["kr"] == category_kr:
            return any(o["kr"] == option_kr for o in cat["options"])
    return False


def find_option_category(option_kr: str) -> Optional[str]:
    """주어진 옵션 이름이 어느 카테고리에 속하는지 반환. 없으면 None."""
    for cat in _option_categories():
        for o in cat["options"]:
            if o["kr"] == option_kr:
                return cat["kr"]
    return None


def is_option_applicable(option_kr: str, menu_category: str) -> bool:
    """옵션이 해당 메뉴 카테고리에 적용 가능한지.
    옵션 카테고리에 `applicable_categories` 필드가 없으면 모든 메뉴에 적용 가능.
    """
    for cat in _option_categories():
        if any(o["kr"] == option_kr for o in cat["options"]):
            allowed = cat.get("applicable_categories")
            if allowed is None:
                return True
            return menu_category in allowed
    return False


def get_menu_category(menu_kr: str) -> Optional[str]:
    item = _menu_index().get(menu_kr)
    return item["category"] if item else None


def get_menu_price(menu_kr: str) -> Optional[int]:
    """메뉴 라지 사이즈 기준 단가 (원). 없으면 None."""
    item = _menu_index().get(menu_kr)
    if not item:
        return None
    return item.get("base_price_l")


def get_menu_stock(menu_kr: str) -> Optional[int]:
    """메뉴의 재고 수량 반환.

    Returns:
        None — 무한 재고 (YAML에 stock 필드 없거나 -1)
        0 — 품절
        양수 — 해당 수량까지 주문 가능
    """
    item = _menu_index().get(menu_kr)
    if not item:
        return None
    stock = item.get("stock")
    if stock is None or stock == -1:
        return None
    return int(stock)


def find_similar_menus(query: str, max_k: int = 3) -> List[str]:
    """주어진 query에 부분 일치하는 메뉴 이름 후보 반환 (단순 substring).

    P2 hallucination 완화 — 사용자가 '단팥빙수' 같이 카테고리 prefix 누락한 발화를
    한 경우 dispatcher가 INVALID_MENU 응답에 후보 메뉴를 포함해서 모델이 사용자에게
    안내할 수 있도록 함.
    """
    if not query:
        return []
    q = query.strip()
    hits = []
    for kr in _menu_index().keys():
        if q in kr or kr in q:
            hits.append(kr)
        if len(hits) >= max_k:
            break
    return hits


# RAG Step 1: 발화 전체에서 메뉴 키워드 추출 → 관련 메뉴 정보 반환
def search_menus_for_utterance(user_text: str, max_k: int = 5) -> List[Dict[str, Any]]:
    """사용자 발화 전체에서 메뉴 후보를 검색.

    - 발화의 각 토큰을 메뉴명/별명/카테고리와 substring 매칭
    - 매칭된 메뉴를 가격/카테고리 포함해서 반환
    - hallucination 완화용 hint inject 재료
    """
    if not user_text:
        return []
    text = user_text.strip()

    # 1차: 메뉴명 직접 substring (양방향)
    scored: Dict[str, int] = {}
    for kr in _menu_index().keys():
        # 더 긴 매칭에 더 큰 점수
        if kr in text:
            scored[kr] = max(scored.get(kr, 0), len(kr) * 10)
        else:
            # 메뉴명을 토큰화해서 부분 매칭 (예: 발화 "단팥빙수" vs 메뉴 "컵단팥빙수")
            # 가장 긴 공통 substring 길이를 점수로
            common_len = _longest_common_substring_len(kr, text)
            if common_len >= 2:  # 너무 짧은 매칭(예: 단일 글자)은 제외
                scored[kr] = max(scored.get(kr, 0), common_len)

    # 2차: 카테고리 키워드 매칭 (보너스 점수)
    category_keywords = {
        "coffee": ["커피", "아메리카노", "라떼"],
        "cold_brew": ["콜드브루"],
        "decaf": ["디카페인"],
        "beverage": ["음료", "밀크", "녹차"],
        "tea": ["차", "티"],
        "bubble_tea": ["버블", "버블티"],
        "flatccino": ["플랫치노", "프라푸치노", "프라페"],
        "ade": ["에이드", "스무디"],
        "bakery": ["빵", "와플", "베이글", "프레첼"],
        "ice_flakes": ["빙수"],
    }
    matched_categories = set()
    for cat, kws in category_keywords.items():
        if any(kw in text for kw in kws):
            matched_categories.add(cat)

    # 카테고리 매칭된 메뉴에 보너스 점수 (이미 직접 매칭된 건 제외하고 추가)
    for kr, item in _menu_index().items():
        if item.get("category") in matched_categories and kr not in scored:
            scored[kr] = 1  # 낮은 점수, 후보 enumerate용

    # top-k 추출
    sorted_menus = sorted(scored.items(), key=lambda x: -x[1])[:max_k]
    return [_menu_index()[kr] for kr, _ in sorted_menus]


def _longest_common_substring_len(a: str, b: str) -> int:
    """두 문자열의 가장 긴 공통 substring 길이."""
    if not a or not b:
        return 0
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    best = 0
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
                best = max(best, dp[i][j])
    return best
