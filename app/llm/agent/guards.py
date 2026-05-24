"""작은 모델의 라우팅 실패를 잡는 가드.

- silent corruption (P0): 도구 호출 없이 "담았어요" 응답.
- stuck loop (P3): 같은 tool_call을 N회 반복.
- confirm-question loop: clarification 답변 턴에서 메뉴 확정됐는데도 재확인 질문.
- silent-add recovery: P0 retry까지 실패했을 때 모델 텍스트로 add 의도를 복구.

모두 텍스트 패턴 매칭 + cart/history read만 — side effect는 silent-add 복구 1건뿐
(dispatch_tool 호출). 가드 적용 여부와 순서는 runner.run_turn이 결정.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from app.dispatcher import dispatch_tool
from app.domain.cart import Cart
from app.domain.menu import all_menu_names

logger = logging.getLogger("ediya.agent.guards")


# ---------- silent corruption (P0) ----------

# 도구 호출 없이 이 표현이 들어가면 모델이 "처리했다"고 말하지만 실제로는 안 한 상태.
_CONFIRMATION_VERB_STEMS = [
    "담았", "담을게", "담아드",
    "빼드", "빼겠",
    "변경했", "변경해드",
    "바꿨", "바꿔드", "바꾸어",
    "추가했", "추가됐", "추가해드",
    "제거했", "제거됐", "제거해드",
    "줄였", "줄여드",
    "주문했", "주문해드", "주문해 드", "주문 받았",
    "처리했", "처리해드", "처리해 드", "처리됐",
    "주문 완료", "완료되었", "결제 도와",
]

# 어간 substring으로 못 잡는 변형 흡수용 정규식.
# "샷추가를 했어요" / "옵션을 변경해 드렸어요" / "사이즈를 엑스트라로 바꿨어요" 같은
# 동사명사 + 조사 + 활용형 패턴까지 cover.
_VERB_REGEX = re.compile(
    r"(추가|변경|바꿔|바꾸|담|빼|제거|줄여|넣|뺀|뺐|취소|되돌|살려)"
    r"(을|를|아|어|여|해|시켜)?\s*"
    r"(했|드렸|드릴게|해드|드림|드려|되었|됐)"
)

SILENT_CORRUPTION_RETRY_HINT = (
    "[System hint] 직전 사용자 발화에는 명확한 주문/변경/제거/완료 의도가 있었어요. "
    "텍스트로 '처리했어요'라고 말하지 말고, 반드시 알맞은 tool을 호출해서 카트에 반영하세요. "
    "예: '담았어요' 라고 말하기 전에 add_menu tool을 호출. "
    "'X에 Y 옵션 추가' 발화는 카트가 비어있으면 add_menu(menu=X, options=[Y])로, "
    "카트에 이미 X가 있으면 change_option(menu=X, ...)로 처리해야 해요. "
    "도구 호출 없이는 카트가 실제로 바뀌지 않아요."
)


def looks_silent_corruption(text: str) -> bool:
    """confirmation 동사가 있고 도구 호출이 없을 때 True.

    두 단계:
    1) 어간 substring — 가장 흔한 표현
    2) 정규식 — "샷추가를 했어요" 같은 조사 끼어든 변형
    """
    if any(stem in text for stem in _CONFIRMATION_VERB_STEMS):
        return True
    return bool(_VERB_REGEX.search(text))


# ---------- tool-call hallucination (P7) ----------

# 모델이 OpenAI tool-calling protocol을 잊고 도구 호출을 텍스트로 흉내내는 패턴.
# 작은 모델(E2B) 특유의 실패 모드 — `tool_name`, `add_item(...)`, json 코드블록 등.
_HALLUCINATION_PATTERNS = [
    re.compile(r"```\s*(json|python|tool|function)?", re.IGNORECASE),  # 코드블록
    re.compile(r"\btool_name\b\s*[:=]"),
    re.compile(r"\bfunction_name\b\s*[:=]"),
    re.compile(r"\bparams\b\s*:\s*\{"),
    re.compile(r"\b(add_item|add_menu|remove_item|update_order|change_option|inquire_menu_info|done|undo)\s*\("),
    re.compile(r"\b(item_name|menu_name|modifications|toppings|topping)\s*=", re.IGNORECASE),
    re.compile(r'"tool_call"\s*:'),
    re.compile(r"^Tool Call\s*:", re.MULTILINE | re.IGNORECASE),
]

TOOL_HALLUCINATION_RETRY_HINT = (
    "[System hint] 응답에 코드블록(```)이나 함수 호출 텍스트(add_menu(...), tool_name 등)를 "
    "절대 출력하지 마세요. 그건 텍스트 시뮬레이션이지 실제 도구 호출이 아니에요. "
    "사용자에게 자연스러운 한국어로만 응답하고, 카트를 바꾸려면 반드시 제공된 tool을 "
    "실제로 호출하세요. "
    "두유/오트밀크 같이 우리 옵션 사전에 없는 항목은 만들어내지 말고 '죄송하지만 우유 변경 "
    "옵션은 매장에서 제공하지 않아요' 하고 정직하게 안내하세요."
)


def looks_tool_hallucination(text: str) -> bool:
    """모델이 도구 호출 protocol을 텍스트로 흉내내는지."""
    return any(p.search(text) for p in _HALLUCINATION_PATTERNS)


# ---------- confirm-question loop (clarification followup) ----------

# 메뉴가 다 정해졌는데도 "담을까요?"로 되묻는 패턴 감지용.
_CONFIRM_QUESTION_STEMS = [
    "담을까", "담아드릴까", "담아 드릴까", "드릴까요",
    "추가할까", "주문할까", "해드릴까", "해 드릴까", "맞을까", "맞으실까",
]
_OPEN_QUESTION_WORDS = ["어떤", "중에서", "골라", "선택해", "무엇", "뭐로"]

CONFIRM_RETRY_HINT = (
    "[System hint] 메뉴(온도+종류)가 이미 확정됐어요. 사용자에게 '담을까요?'라고 "
    "다시 묻지 마세요. 지금 바로 add_menu tool을 호출해서 카트에 담으세요. "
    "불필요한 재확인은 turn 낭비예요."
)


def looks_confirm_question(text: str) -> bool:
    """메뉴 확정 상태에서 던지는 재확인 질문('~담을까요?')인지.

    '어떤 라떼?' 같은 열린 질문(아직 좁히는 중)은 제외 — 그건 정상 흐름.
    """
    has_confirm = any(s in text for s in _CONFIRM_QUESTION_STEMS)
    is_open = any(w in text for w in _OPEN_QUESTION_WORDS)
    return has_confirm and not is_open


# ---------- stuck loop (P3) ----------

def is_stuck_loop(history: List[Dict[str, Any]], window: int = 3) -> bool:
    """history 최근 assistant tool_calls가 동일 (name, arguments)로 N회 반복이면 True.

    이미 처리됐거나 유효하지 않은 도구 호출을 stale state로 반복하는 케이스 차단.
    """
    recent: List[str] = []
    for msg in reversed(history):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                sig = f"{tc['function']['name']}::{tc['function']['arguments']}"
                recent.append(sig)
                if len(recent) >= window:
                    break
            if len(recent) >= window:
                break
    if len(recent) < window:
        return False
    return all(s == recent[0] for s in recent[:window])


# ---------- silent-add recovery ----------

# 모델이 'X 담았어요'라고 텍스트로만 선언한 경우의 add 의도 어간.
_ADD_CONFIRM_STEMS = [
    "담았", "담아드", "담을게",
    "추가했", "추가됐", "추가해드", "추가해 드",
    "주문했", "주문해드", "주문해 드", "주문 받았",
]
# 제거/교체 의도가 섞이면 함부로 add 복구하지 않는다.
_REMOVE_INTENT_MARKERS = ["빼", "취소", "제거", "줄여", "줄이", "바꿔", "변경", "교체"]
# 부정/실패 표현이 있으면 "담지 못했다"는 뜻 — 복구 금지.
_NEGATION_MARKERS = [
    "못 담", "담지 못", "담을 수 없", "추가 못", "추가할 수 없",
    "없어요", "없습니다", "품절", "안 돼", "안돼", "불가",
]
_KO_QUANTITY = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6}

# 라떼 종류를 특정하는 수식어. 이게 없는 맨 '라떼'는 관용적으로 '카페라떼'.
_LATTE_QUALIFIERS = [
    "연유", "헤이즐넛", "바닐라", "녹차", "카라멜", "카페", "콜드브루", "디카페인",
]


def _extract_quantity_from_text(text: str) -> int:
    """발화/응답 텍스트에서 'N잔' 수량 추출. 못 찾으면 1."""
    m = re.search(r"(\d+)\s*잔", text)
    if m:
        return max(1, int(m.group(1)))
    for word, n in _KO_QUANTITY.items():
        if f"{word} 잔" in text or f"{word}잔" in text:
            return n
    return 1


def _extract_menu_from_text(text: str) -> Optional[str]:
    """텍스트에서 정식 메뉴명 추출. 띄어쓰기/축약 보정 포함.

    모델은 'X 담았어요'라고 할 때 메뉴를 띄어쓰거나 '카페'를 빠뜨려 부르는 경우가
    많다 (예: '아이스 라떼' → 정식명 '아이스카페라떼'). 공백 제거 후 직접 매칭하고,
    수식어 없는 맨 '라떼'는 '카페라떼'로 보정해 재매칭.
    """
    despaced = text.replace(" ", "")
    hits = [kr for kr in all_menu_names() if kr in despaced]
    if hits:
        return max(hits, key=len)
    if "라떼" in despaced and not any(q in despaced for q in _LATTE_QUALIFIERS):
        expanded = despaced.replace("라떼", "카페라떼")
        hits = [kr for kr in all_menu_names() if kr in expanded]
        if hits:
            return max(hits, key=len)
    return None


def recover_silent_add(text: str, cart: Cart) -> Optional[str]:
    """P0 retry까지 실패했을 때 마지막 복구: 모델이 텍스트로 선언한 add 의도를 dispatch.

    제거/교체/부정 의도가 섞인 발화는 안전을 위해 복구하지 않는다.
    성공 시 새 응답 문자열을, 실패 시 None을 반환.
    """
    if any(mk in text for mk in _REMOVE_INTENT_MARKERS):
        return None
    if any(mk in text for mk in _NEGATION_MARKERS):
        return None
    if not any(s in text for s in _ADD_CONFIRM_STEMS):
        return None
    menu = _extract_menu_from_text(text)
    if not menu:
        return None
    qty = _extract_quantity_from_text(text)
    result = dispatch_tool(
        "add_menu", {"menu": menu, "quantity": qty, "options": []}, cart
    )
    if result.get("status") in ("ADDED", "INCREMENTED"):
        logger.warning(
            "silent-add recovery: dispatched add_menu(%s, qty=%d) from model text",
            menu,
            qty,
        )
        return f"{menu} {qty}잔 담았어요."
    return None
