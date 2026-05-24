"""NLU 진입점 — 발화 → IntentProposal.

결정론. 단위 테스트로 100% 커버 가능. LLM은 호출하지 않는다.
"""
from __future__ import annotations

import re
from typing import List, Optional

from app.nlu.aliases import (
    detect_menu_slang,
    detect_missing_options,
    detect_option_aliases,
)
from app.nlu.domain_rules import apply_decaf_rule
from app.nlu.intent import (
    ActionHint,
    IntentProposal,
    MenuIntent,
)
from app.nlu.menu_resolver import (
    detect_family_ambiguity,
    detect_temperature_ambiguity,
    is_temperature_only_ambiguity,
    resolve_menu_from_utterance,
)
from app.nlu.normalize import extract_quantity, normalize_whitespace


# 동작 keyword. 우선순위 순.
_INTENT_KEYWORDS: List[tuple[str, ActionHint]] = [
    (r"(되돌|복원|살려|원래대로|undo)", "undo"),
    (r"(주문\s*끝|결제|계산|마무리|\bdone\b)", "done"),
    (r"(뭐 시켰|뭐 시킨|주문 내역|장바구니|카트)", "check_cart"),
    # inquire — 질문형. add 키워드보다 먼저 평가해야 함 ("디카페인 있어요?")
    (r"(얼마|가격|추천|뭐 있|뭐가 있|있어요|있어\?|있나|있을까|뭐 있어)", "inquire"),
    (r"(말고|대신|바꿔|바꾸|교체|로 변경)", "replace"),  # 메뉴 자체 교체
    (r"(빼|취소|지워|제거|삭제|줄여|줄이)", "remove"),
    (r"(엑스트라|샷추가|샷 추가|투샷|트리플샷|시럽|휘핑|덜달|저당|얼음.*많|얼음.*적|크기 변경)", "change_option"),
    (r"(주문|담아|넣어|주세요|드릴까|주문할게|시킬게)", "add"),
]


def _classify_action(text: str) -> ActionHint:
    """발화의 동작 의도 분류. 1개만 매칭되는 가장 강한 신호.

    add는 명시적인 주문 동사가 있을 때만. 메뉴만 발화된 경우(예: "아이스 아메리카노")는
    add로 추정 — 추가 동사 없이도 메뉴 자체가 주문 신호이므로.
    """
    for pattern, action in _INTENT_KEYWORDS:
        if re.search(pattern, text):
            return action
    # 메뉴 명사가 있으면 add 추정. 그 외 unknown.
    return "add"


def _mask_slang_in_text(text: str) -> str:
    """슬랭이 잡힌 위치는 옵션 검색에서 제외 (잠식 방지).

    예: "아샷추 하나만 담아주세요" — '아샷추'는 슬랭(레몬아이스티)으로 풀린 후,
    "샷" 토큰을 옵션 사전에서 또 잡으면 add_menu(레몬아이스티, options=[샷추가])로
    잘못 묶임. 슬랭 영역을 공백으로 치환해 옵션 검색에서 빠지게 한다.
    """
    masked = text
    for slang in detect_menu_slang(text).keys():
        masked = masked.replace(slang, " " * len(slang))
    return masked


def _extract_single_menu_intent(text: str) -> MenuIntent:
    """단일 메뉴 발화 → MenuIntent 1개."""
    intent = MenuIntent()
    intent.quantity = extract_quantity(text)

    # 1) 디카페인 룰 먼저 (특수 케이스)
    decaf = apply_decaf_rule(text)
    if decaf:
        menu, note = decaf
        if menu == "REJECT_HOT_DECAF":
            intent.ambiguity = "decaf_hot"
            intent.notes.append(note)
            return intent
        intent.menu_kr = menu
        intent.notes.append(note)

    # 2) 메뉴 해상도 (디카페인이 아직 안 잡혔으면)
    if intent.menu_kr is None:
        menu, candidates = resolve_menu_from_utterance(text)
        if menu:
            intent.menu_kr = menu
        else:
            # 온도만 누락 → temperature 모호 (family보다 우선 — 종류는 결정됨)
            if is_temperature_only_ambiguity(text):
                intent.ambiguity = "temperature"
                intent.ambiguity_candidates = candidates
            elif candidates:
                intent.ambiguity_candidates = candidates
                intent.ambiguity = "menu_family"
            else:
                if detect_temperature_ambiguity(text):
                    intent.ambiguity = "temperature"
                else:
                    intent.ambiguity = "menu_unknown"

    # 3) 메뉴가 잡혔어도 family check — '라떼' 등은 여전히 모호일 수 있음
    if intent.menu_kr and not intent.notes:  # 디카페인 매핑이 아닌 일반 경로
        family = detect_family_ambiguity(text)
        if family and intent.menu_kr not in family[1]:
            # exact 매칭이 family 후보 밖이면 통과 (예: 헤이즐넛라떼)
            pass

    # 4) 옵션 추출 — 슬랭이 잡은 영역은 제외 ("아샷추" → "샷" 잠식 방지)
    text_for_options = _mask_slang_in_text(text)
    intent.options = detect_option_aliases(text_for_options)
    intent.missing_options = detect_missing_options(text)
    # missing_options가 있으면 가장 강한 신호 — 매장에 없는 옵션 안내가 우선
    if intent.missing_options:
        if intent.ambiguity == "menu_family":
            intent.notes.append(
                f"메뉴 종류도 모호하지만, '{intent.missing_options[0]}' 옵션이 "
                "매장에 없는 게 더 큰 문제 — 옵션 거절을 먼저 안내."
            )
        intent.ambiguity = "option_missing"

    # 5) 슬랭 명시 (notes)
    slang = detect_menu_slang(text)
    if slang:
        for s, targets in slang.items():
            if len(targets) == 1:
                intent.notes.append(f"'{s}' = {targets[0]} (줄임말).")

    return intent


# 한 발화에 메뉴 여러 개를 가르는 단순 패턴.
_COMPOUND_SEPARATORS = [
    r"이랑\s+", r"랑\s+", r"하고\s+", r"\s+와\s+", r"\s+과\s+", r"그리고\s+",
]

# 수량/양사 다음 "에 [메뉴_시작_단어]" 패턴만 분할.
# 메뉴 시작 단어: 아이스/핫/디카페인/콜드브루/카페/녹차/딸기/초코/모카/달고나/연유 등
# 옵션 시작 단어(엑스트라/샷/시럽/덜달/얼음 등)는 분할 X — change_option 의도.
_MENU_START_WORDS = (
    "아이스", "핫", "디카페인", "콜드브루",
    "카페", "헤이즐넛", "바닐라", "녹차", "딸기", "초코", "모카",
    "달고나", "연유", "흑당", "민트", "레몬", "복숭아", "자몽",
)
_MENU_START_ALT = "|".join(_MENU_START_WORDS)
_QTY_E_PATTERN = re.compile(
    r"([한두세네하나둘셋넷다섯여섯일곱]\s?잔|\d+잔|[한두세네하나둘셋넷다섯여섯일곱])"
    r"\s*에\s+"
    rf"(?=({_MENU_START_ALT}))"
)


def _split_by_qty_e(text: str) -> List[str]:
    """수량 + '에 [메뉴 시작 단어]' 다음 위치에서 분할.

    옵션 발화("한 잔에 엑스트라 사이즈로")는 분할하지 않는다 — 양사 뒤 단어가
    옵션이면 같은 메뉴의 옵션이지 새 메뉴가 아니므로.
    """
    splits: List[int] = []
    for m in _QTY_E_PATTERN.finditer(text):
        splits.append(m.end())
    if not splits:
        return [text]
    parts = []
    prev = 0
    for cut in splits:
        parts.append(text[prev:cut])
        prev = cut
    parts.append(text[prev:])
    return parts


def _split_compound(text: str) -> List[str]:
    """복합 발화를 부분으로 분할 시도.

    명시적 연결어가 있을 때만 분할. 모호하면 한 덩이.
    """
    parts = [text]
    for sep in _COMPOUND_SEPARATORS:
        new_parts: List[str] = []
        for p in parts:
            new_parts.extend(re.split(sep, p))
        parts = new_parts

    # "N잔 에 M잔" 패턴 추가 분할 ("에" 단독은 옵션 발화일 수 있어 수량 뒤에서만)
    expanded: List[str] = []
    for p in parts:
        expanded.extend(_split_by_qty_e(p))
    parts = [normalize_whitespace(p) for p in expanded if normalize_whitespace(p)]
    if len(parts) <= 1:
        return [text]
    return parts


def extract_intent(
    utterance: str,
    cart_menus: Optional[List[str]] = None,
) -> IntentProposal:
    """발화 + 현재 카트 상태 → IntentProposal. 결정론, 순수 함수.

    Args:
        utterance: 사용자 발화.
        cart_menus: 현재 카트에 담긴 메뉴명 리스트. None이면 빈 카트로 간주.
            change_option vs add_menu 결정에 사용 — 카트에 해당 메뉴 없으면 add로 promote.
    """
    text = normalize_whitespace(utterance or "")
    if not text:
        return IntentProposal(action="unknown", raw_utterance=text)

    cart_menus = cart_menus or []
    action = _classify_action(text)

    # add / replace / change_option 발화에서만 메뉴 분해. 나머지(remove/inquire/done 등)는
    # cart/history 의존도가 커 모델에게 위임.
    if action in ("add", "replace", "change_option"):
        parts = _split_compound(text)
        intents = [_extract_single_menu_intent(p) for p in parts]

        # change_option promotion — 카트 비어있거나 의도한 메뉴가 카트에 없으면 add_menu로.
        # 가드가 아니라 *결정론적 분류* — 같은 발화여도 카트 상태에 따라 의도가 다름.
        if action == "change_option":
            target_menus = [i.menu_kr for i in intents if i.menu_kr]
            if not cart_menus or not any(m in cart_menus for m in target_menus):
                action = "add"
                proposal = IntentProposal(
                    action=action,
                    intents=intents,
                    raw_utterance=text,
                    global_notes=[
                        "카트가 비어있거나 의도한 메뉴가 카트에 없어 change_option → add_menu로 승격."
                    ],
                )
                return proposal

        return IntentProposal(
            action=action,
            intents=intents,
            raw_utterance=text,
        )

    # remove / undo / inquire / check_cart / done — 동작만 알려주고 모델 위임
    return IntentProposal(
        action=action,
        intents=[],
        raw_utterance=text,
    )
