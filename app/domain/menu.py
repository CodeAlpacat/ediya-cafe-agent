"""메뉴/옵션 사전 로더 + 검증 헬퍼."""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List, Optional

import yaml

from app.cafe_profile import load_profile, menu_data_path

# 활성 카페(CAFE_PROFILE)의 메뉴 데이터 경로.
MENU_DATA_PATH = menu_data_path()


@lru_cache(maxsize=1)
def load_menu() -> Dict[str, Any]:
    with MENU_DATA_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _menu_index() -> Dict[str, Dict[str, Any]]:
    return {m["kr"]: m for m in load_menu()["menus"]}


def _option_categories() -> List[Dict[str, Any]]:
    return load_menu()["option_categories"]


def _categories() -> Dict[str, Dict[str, Any]]:
    return load_menu().get("categories", {})


def get_category_descriptor(category: str) -> str:
    """카테고리의 임베딩 검색용 자연어 설명. 없으면 빈 문자열."""
    return _categories().get(category, {}).get("descriptor", "")


def get_category_keywords() -> Dict[str, List[str]]:
    """카테고리별 키워드 검색 트리거 어휘 매핑 ({category_id: [keywords]})."""
    return {cid: c.get("keywords", []) for cid, c in _categories().items()}


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


# 손님이 쓰는 줄임말/은어 → 정식 메뉴명. 활성 카페의 profile.yaml에서 로드한다.
# 작은 모델은 줄임말 라우팅이 약해서, 키워드 검색 단계에서 미리 정식 메뉴로 풀어준다.
# (메뉴명의 substring으로 이미 잡히는 줄임말 — 예: '아메', '라떼' — 은 등록 불필요.)
#
# - 값이 1개: 온도까지 분명한 줄임말 → 정식 메뉴로 직결.
# - 값이 여러 개: 온도/종류가 모호한 줄임말 → 후보를 모두 띄워 모델이 되묻게 한다.
_SLANG_ALIASES: Dict[str, List[str]] = {
    str(k): [str(x) for x in (v or [])]
    for k, v in (load_profile().get("slang_aliases") or {}).items()
}


def resolve_menu_alias(name: str) -> Optional[str]:
    """줄임말/은어를 정식 메뉴명으로 변환.

    단일 메뉴로 확정되는 줄임말('아아' 등)일 때만 정식명을 반환한다.
    모호한 줄임말('카마' 등)이나 줄임말이 아니면 None — dispatcher가
    잘못된 추측 대신 원래 동작(검증/되묻기)을 하도록.
    """
    if not name:
        return None
    targets = _SLANG_ALIASES.get(name.strip())
    if targets and len(targets) == 1 and targets[0] in _menu_index():
        return targets[0]
    return None


def all_menu_names() -> List[str]:
    """전체 정식 메뉴명 리스트."""
    return list(_menu_index().keys())


def detect_slang(text: str) -> Dict[str, List[str]]:
    """발화에 들어있는 등록된 줄임말 → 정식 메뉴 후보 매핑.

    agent layer가 "'아아'는 아이스아메리카노" 같은 명시적 hint를 주입할 때 쓴다.
    """
    if not text:
        return {}
    return {s: list(t) for s, t in _SLANG_ALIASES.items() if s in text}


def keyword_menu_search(user_text: str, max_k: int = 5) -> List[Dict[str, Any]]:
    """사용자 발화에서 메뉴 후보를 키워드/substring 매칭으로 검색.

    임베딩 검색(rag.search_menus)과는 별개의 가벼운 사전 매칭이다.
    - 발화를 메뉴명과 substring 매칭 (양방향 + 최장 공통 substring)
    - 카테고리 키워드 매칭으로 후보 보강
    - 매칭된 메뉴를 가격/카테고리 포함해서 반환
    hallucination 완화용 hint inject 재료로 쓰인다.
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

    # 1.5차: 줄임말/은어 매칭 — "아아", "뜨아", "아바라" 같은 표현을 정식 메뉴로 풀어준다.
    # 줄임말은 사용자가 의도를 분명히 한 신호이므로 substring 매칭보다 우선한다.
    for slang, targets in _SLANG_ALIASES.items():
        if slang in text:
            for target in targets:
                if target in _menu_index():
                    scored[target] = max(scored.get(target, 0), 1000)

    # 2차: 카테고리 키워드 매칭 (보너스 점수) — 키워드는 menu_data.yaml에서 로드
    matched_categories = set()
    for cat, kws in get_category_keywords().items():
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
