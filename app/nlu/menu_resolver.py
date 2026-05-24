"""발화 → 메뉴 후보 추출.

정확 매칭 우선, fallback으로 keyword/RAG. 결정론.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from app.domain.menu import (
    all_menu_names,
    keyword_menu_search,
    resolve_menu_alias,
)


# 카페에서 자주 쓰는 메뉴 family — 종류 없이 발화되면 모호.
_AMBIGUOUS_FAMILIES = {
    "라떼": ["아이스카페라떼", "핫카페라떼", "아이스연유라떼", "핫연유라떼",
             "아이스헤이즐넛라떼", "핫헤이즐넛라떼", "아이스바닐라라떼", "핫바닐라라떼"],
    "커피": ["아이스아메리카노", "핫아메리카노", "아이스카페라떼", "핫카페라떼"],
}


# 라떼 종류를 특정하는 수식어. 이게 있으면 가족명 라떼가 아님.
_LATTE_QUALIFIERS = [
    "연유", "헤이즐넛", "바닐라", "녹차", "카라멜", "카페", "콜드브루", "디카페인",
    "초코", "딸기", "달고나", "흑당", "민트초코", "녹차",
]


def _despace(text: str) -> str:
    return text.replace(" ", "")


def resolve_exact_menu(text: str) -> Optional[str]:
    """발화에 정식 메뉴명이 그대로 포함되어 있으면 그 메뉴 반환.

    띄어쓰기 차이도 흡수 ("아이스 아메리카노" → "아이스아메리카노").
    여러 매칭 시 *가장 긴* 메뉴명 반환 (예: "디카페인콜드브루아메리카노" 우선).
    """
    if not text:
        return None
    despaced = _despace(text)
    hits = [kr for kr in all_menu_names() if kr in despaced]
    if hits:
        return max(hits, key=len)
    return None


def resolve_loose_menu(text: str) -> Optional[str]:
    """exact 매칭 실패 시 — 사용자 발화의 흔한 부정확 표현 보정.

    - "아이스 라떼" (수식어 없음, 라지/엑스트라 등 사이즈도 없음) → "아이스카페라떼"
    - "핫 라떼" → "핫카페라떼"
    """
    if not text:
        return None
    despaced = _despace(text)
    if "라떼" in despaced and not any(q in despaced for q in _LATTE_QUALIFIERS):
        expanded = despaced.replace("라떼", "카페라떼")
        hits = [kr for kr in all_menu_names() if kr in expanded]
        if hits:
            return max(hits, key=len)
    return None


def detect_temperature_ambiguity(text: str) -> bool:
    """발화에 '아메리카노/라떼/콜드브루' 같은 메뉴 family는 있지만
    아이스/핫이 빠졌는지. 모호로 보고 모델에게 되묻기 hint를 만들어줘야 함.
    """
    if not text:
        return False
    despaced = _despace(text)
    if "아이스" in despaced or "핫" in despaced or "콜드브루" in despaced:
        return False
    # family 단어 등장 검사
    families = ["아메리카노", "라떼", "카페모카", "녹차라떼", "초코라떼"]
    return any(f in despaced for f in families)


def detect_family_ambiguity(text: str) -> Optional[Tuple[str, List[str]]]:
    """'라떼' / '커피' 같이 종류가 안 정해진 가족명 발화 감지.

    Returns (family_name, candidate_menu_names) or None.

    '아메리카노'는 종류가 사실상 1개라 family 모호 아님 — 단 온도가 빠지면
    temperature ambiguity 별도 처리.
    """
    if not text:
        return None
    despaced = _despace(text)
    for family, candidates in _AMBIGUOUS_FAMILIES.items():
        if family == "라떼":
            # 수식어 없이 '라떼'만 있으면 family. 단 '아이스/핫' 온도는 있을 수 있음.
            if family in despaced and not any(q in despaced for q in _LATTE_QUALIFIERS):
                # 온도 있으면 후보 좁힘
                if "아이스" in despaced:
                    narrowed = [c for c in candidates if c.startswith("아이스")]
                    if len(narrowed) == 1:
                        return None  # 1개로 좁혀짐 — 모호 아님
                    return (family, narrowed)
                if "핫" in despaced:
                    narrowed = [c for c in candidates if c.startswith("핫")]
                    if len(narrowed) == 1:
                        return None
                    return (family, narrowed)
                return (family, candidates)
        elif family == "커피":
            # '커피'는 자주 모호 — 메뉴명 일부('카페')가 아닌 단독 단어로만 잡음
            if "커피" in despaced and not any(m in despaced for m in
                ["아메리카노", "라떼", "카페모카", "콜드브루", "에스프레소"]):
                return (family, candidates)
    return None


def is_temperature_only_ambiguity(text: str) -> bool:
    """온도(아이스/핫) 만 누락된 모호인지.

    예: "아메리카노 한 잔" → 종류는 사실상 1개(아메리카노)인데 온도만 누락 → True
        "라떼 한 잔" → 종류 + 온도 모두 누락 → family 모호 → False
    """
    if not text:
        return False
    despaced = _despace(text)
    if "아이스" in despaced or "핫" in despaced or "콜드브루" in despaced:
        return False
    # '아메리카노' 단독 — 종류 결정됨, 온도만 누락
    if "아메리카노" in despaced:
        return True
    # '카페모카' 단독 — 종류 결정됨
    if "카페모카" in despaced and "모카플랫치노" not in despaced:
        return True
    return False


def resolve_menu_from_utterance(text: str) -> Tuple[Optional[str], List[str]]:
    """단일 발화에서 메뉴 1개를 결정하려 시도.

    Returns:
        (resolved_menu, candidate_list)
        - resolved_menu: 확정된 메뉴명 또는 None
        - candidate_list: 모호한 경우 후보 메뉴들 (확정 시 비어있음)
    """
    if not text:
        return None, []

    # 1. 슬랭 사전 (단일 매핑) — token 매칭 + substring 매칭 둘 다 시도
    #    "아아 한 잔" — token 매칭 성공
    #    "아아에 샷추가" — 조사 "에"가 붙어 token 매칭 실패 → substring으로
    from app.domain.menu import detect_slang
    slang_hits = detect_slang(text)
    for slang, targets in slang_hits.items():
        if len(targets) == 1:  # 단일 매핑만 자동 해상도
            return targets[0], []
    # token 기반 alias도 시도
    for word in text.split():
        slang_target = resolve_menu_alias(word)
        if slang_target:
            return slang_target, []

    # 2. exact substring (정식 메뉴명)
    exact = resolve_exact_menu(text)
    if exact:
        return exact, []

    # 3. loose 보정 ("아이스 라떼" → "아이스카페라떼")
    loose = resolve_loose_menu(text)
    if loose:
        return loose, []

    # 4. 가족명 모호 ('라떼' 단독 / '커피' 단독)
    family = detect_family_ambiguity(text)
    if family:
        return None, family[1]

    # 5. 키워드 검색으로 후보들 (확정 X, 모델 참고용)
    candidates = keyword_menu_search(text, max_k=5)
    candidate_names = [c["kr"] for c in candidates]
    return None, candidate_names


def fill_temperature(menu_kr: str, text: str) -> Optional[str]:
    """온도 누락 발화 보정용. 발화에 "아이스" 또는 "핫"이 있으면 그걸 prefix.

    예: menu_kr=None, text="아이스 아메리카노" → 이미 exact에서 잡힘.
        text="아메리카노", 아이스/핫 모두 없음 → None (모호 처리).
    """
    if not menu_kr:
        return None
    if menu_kr.startswith("아이스") or menu_kr.startswith("핫"):
        return menu_kr
    # 메뉴가 이미 콜드브루/디카페인 이름이면 그대로
    if "콜드브루" in menu_kr or "디카페인" in menu_kr:
        return menu_kr
    if "아이스" in text and not text.find("아이스") > text.find(menu_kr):
        return f"아이스{menu_kr}"
    if "핫" in text:
        return f"핫{menu_kr}"
    return None
