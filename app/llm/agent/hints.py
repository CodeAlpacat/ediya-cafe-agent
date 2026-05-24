"""턴마다 모델 라우팅을 보정하는 system hint들.

작은 모델(Gemma 4 E2B)은 한국어 발화의 도구 라우팅이 흔들린다. 시스템 프롬프트
강화는 회귀가 크고 tool description 튜닝은 whack-a-mole이라, 발화별로 1회성
system 메시지를 주입하는 dynamic hint injection으로 처치한다.

모든 함수는 순수 — history/Cart에서 read만 하고 새 hint 문자열(또는 None)을 반환한다.
주입 시점과 순서는 runner.run_turn이 결정.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.domain.cart import Cart
from app.domain.menu import detect_slang, keyword_menu_search


# ---------- 발화 분석 ----------

# P4: "아까/방금/그거" 같은 referential pronoun + 의도 동사 동반.
_REFERENTIAL_WORDS = [
    "아까", "방금", "그거", "그것", "전에 시킨", "전에 주문", "전에 시킨 거",
    "이전에", "처음 시킨", "처음 주문",
]
_INTENT_WORDS_FOR_REF = ["빼", "취소", "지워", "바꾸", "바꿔", "변경", "교체"]

# P6: 한 발화에 제거 + 추가가 같이 들어있는 복합 명령.
_REMOVE_VERBS = ["빼", "취소", "지워", "제거", "줄여", "줄이"]
_ADD_VERBS = ["추가", "더 줘", "더 주", "더줘", "더주", "넣어", "담아"]

# 직전 점원 발화가 "메뉴를 좁히는 질문"임을 알리는 신호.
# '아이스/핫'은 메뉴명 일부라 신호로 쓰면 오발동하므로 제외.
_NARROWING_SIGNALS = ["어떤", "중에서", "골라", "아니면", "무엇으로", "뭐로"]


def looks_referential_intent(user_text: str) -> bool:
    """'아까/방금/그거' + 의도 동사 패턴인지 (P4)."""
    has_ref = any(w in user_text for w in _REFERENTIAL_WORDS)
    has_intent = any(w in user_text for w in _INTENT_WORDS_FOR_REF)
    return has_ref and has_intent


def looks_compound_action(user_text: str) -> bool:
    """한 발화에 제거 + 추가 동작이 같이 들어있는지 (P6)."""
    has_remove = any(w in user_text for w in _REMOVE_VERBS)
    has_add = any(w in user_text for w in _ADD_VERBS)
    return has_remove and has_add


def in_clarification_followup(history: List[Dict[str, Any]], user_text: str) -> bool:
    """직전 점원 발화가 메뉴를 좁히는 질문이었고, 이번 발화가 그 짧은 답인지.

    이 경우 Menu RAG hint는 노이즈 — 짧은 답("아이스")으로 키워드 검색하면
    전 메뉴가 쏟아져 맥락을 덮어쓴다. 대신 clarification followup hint를 쓴다.
    "더 필요한가요?" 같은 마무리 질문 뒤의 새 주문은 오분류하지 않도록,
    실제 '좁히기' 신호가 직전 발화에 있을 때만 True.
    """
    if len(user_text.strip()) > 7:  # 긴 발화는 새 주문일 가능성이 커 제외
        return False
    for msg in reversed(history):
        role = msg.get("role")
        if role == "assistant":
            if msg.get("tool_calls"):
                return False
            content = msg.get("content") or ""
            return any(sig in content for sig in _NARROWING_SIGNALS)
        if role in ("user", "tool"):
            return False
        # system 메시지는 건너뜀
    return False


# ---------- hint 빌더 ----------

def build_slang_hint(user_text: str) -> Optional[str]:
    """발화에 카페 줄임말('아아' 등)이 있으면 정식 메뉴를 못박는 hint를 만든다."""
    found = detect_slang(user_text)
    if not found:
        return None
    lines = []
    for slang, menus in found.items():
        if len(menus) == 1:
            lines.append(f"'{slang}'은(는) '{menus[0]}'를 뜻하는 줄임말이에요.")
        else:
            lines.append(f"'{slang}'은(는) {', '.join(menus)} 중 하나를 뜻해요.")
    return (
        "[Slang] " + " ".join(lines)
        + " 줄임말을 모른 척 되묻지 말고 위 정식 메뉴명으로 처리하세요. "
        "온도까지 분명한 줄임말이면 add_menu를 즉시 호출하세요."
    )


def build_clarification_followup_hint() -> str:
    """clarification 답변 턴에 주입할 hint. 메뉴 목록 재나열 대신 맥락으로 좁히도록."""
    return (
        "[Clarification] 직전에 점원(assistant)이 메뉴를 좁히려고 질문했고, "
        "지금 사용자 발화는 그 질문에 대한 짧은 답이에요. "
        "메뉴 목록을 처음부터 다시 나열하지 마세요. "
        "직전 질문 + 이번 답을 합쳐 메뉴를 판단하세요:\n"
        "- 온도(아이스/핫)와 종류가 모두 정해졌으면 메뉴가 확정된 거예요. "
        "'담을까요?' 같은 재확인을 하지 말고 그 즉시 add_menu를 호출하세요.\n"
        "- 직전 질문이 특정 메뉴를 '~담을까요/드릴까요?'처럼 제안한 거였고 사용자가 "
        "긍정('네/응/그래/맞아요')했으면, 그 제안한 메뉴로 즉시 add_menu를 호출하세요.\n"
        "- 수량은 앞선 대화에서 사용자가 말한 값을 그대로 유지하세요. "
        "'두 잔' 주문 중이었다면 quantity=2로 호출해야 해요. 임의로 1로 바꾸지 마세요.\n"
        "- 아직 1가지(온도 또는 종류)만 부족할 때만 그 1가지를 콕 집어 다시 물으세요."
    )


def build_compound_action_hint() -> str:
    """복합 명령 발화 감지 시 inject할 hint. 동작을 한 턴에 모두 처리하도록."""
    return (
        "[Compound action] 사용자 발화에 여러 동작(예: 제거 + 추가)이 함께 들어있어요. "
        "다음 턴으로 미루지 말고 한 번의 처리에서 끝내세요:\n"
        "- 메뉴가 분명한 동작(뺄 메뉴가 카트에 있는 등)은 지금 바로 해당 tool을 호출하세요. "
        "예: '아아 빼줘' → remove_menu 즉시 호출.\n"
        "- 메뉴가 모호한 동작('라떼 추가'처럼 종류 불명)은 추측해서 도구를 호출하지 말고, "
        "Menu RAG 후보 이름을 나열해 사용자에게 되물으세요.\n"
        "- 분명한 동작을 처리한 뒤, 모호한 부분 질문을 같은 응답에 이어서 하세요. "
        "예: '아이스아메리카노는 빼드렸어요. 추가하실 라떼는 아이스카페라떼, 아이스연유라떼, "
        "아이스헤이즐넛라떼 중 어떤 걸로 드릴까요?'"
    )


def build_menu_context_hint(user_text: str) -> Optional[str]:
    """발화 관련 메뉴 후보를 키워드 검색으로 찾아 system hint로 주입 (P2).

    모델이 menu list를 모르는 상태에서 '단팥빙수' 같은 자유 발화를 '없어요'라고
    거절하지 않도록, 매장 메뉴 후보를 동적으로 inject.
    """
    candidates = keyword_menu_search(user_text, max_k=8)
    if not candidates:
        return None
    lines = []
    for c in candidates:
        price = c.get("base_price_l", 0)
        stock = c.get("stock")
        stock_note = ""
        if stock == 0:
            stock_note = " (품절)"
        elif stock is not None and stock > 0:
            stock_note = f" (재고 {stock}개)"
        lines.append(f"  - {c['kr']} ({c['category']}, {price}원){stock_note}")
    return (
        "[Menu RAG] 사용자 발화와 관련된 매장 메뉴 후보 (모두 실제 취급 중):\n"
        + "\n".join(lines)
        + "\n위 메뉴는 매장에 분명히 존재해요. '없어요'라고 거절하지 마세요. "
        + "사용자가 메뉴를 정확히 말했으면 그 이름으로 바로 도구를 호출하세요. "
        + "'라떼'·'커피'처럼 종류만 말해 모호하면 추측하지 말고 되묻되, 그때는 "
        + "위 후보 이름을 그대로 2~4개 나열해 선택지를 제시하세요 "
        + "(예: '아이스카페라떼, 아이스연유라떼, 아이스헤이즐넛라떼 중에서 골라보시겠어요?')."
    )


def build_post_inquiry_hint(
    history: List[Dict[str, Any]], user_text: str
) -> Optional[str]:
    """Inquire 후 사용자가 specific 메뉴를 말한 패턴 감지 → add_menu 강제 hint.

    L3 처치(tool result 압축)만으로는 E2B가 inquiry 모드 stuck이 잘 풀리지 않음.
    Agent layer에서 직접 감지:
    - 직전 turn에 inquire_menu_info 호출됨
    - 현재 user 발화에서 정확한 메뉴 후보가 추출됨 (keyword_menu_search 매칭)
    → "add_menu 호출하라. inquire 또 호출 금지" hint inject.
    """
    last_inquire = False
    for msg in reversed(history):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if tc["function"]["name"] == "inquire_menu_info":
                    last_inquire = True
            break
        if msg.get("role") == "user":
            break

    if not last_inquire:
        return None

    candidates = keyword_menu_search(user_text, max_k=3)
    if not candidates:
        return None

    candidate_names = ", ".join(c["kr"] for c in candidates)
    return (
        "[Post-inquiry routing] 사용자가 메뉴 안내를 받은 후 구체적인 메뉴를 명시했어요. "
        "이건 명확한 주문 의도예요. inquire_menu_info를 다시 호출하지 말고 add_menu를 호출하세요. "
        f"사용자 발화에서 추출한 메뉴 후보: {candidate_names}. "
        "이 후보 중 가장 적합한 것 또는 사용자가 정확히 말한 메뉴명으로 "
        "add_menu(menu=..., quantity=..., options=[]) 호출."
    )


def _format_cart_item(item: Dict[str, Any]) -> str:
    if item.get("options"):
        return f"{item['menu']} (옵션: {', '.join(item['options'])})"
    return item["menu"]


def build_referential_hint(cart: Cart) -> Optional[str]:
    """사용자가 referential 표현 썼을 때 hint. 카트에 메뉴 있을 때만."""
    snap = cart.snapshot()
    if not snap:
        return None
    last = snap[-1]
    menu_list = ", ".join(_format_cart_item(it) for it in snap)
    return (
        f"[Referential hint] 사용자가 '아까/방금/그거' 같은 표현으로 카트의 메뉴를 가리켰어요. "
        f"가장 최근에 추가된 메뉴: {_format_cart_item(last)}. "
        f"전체 카트: {menu_list}. "
        f"이 정보로 적절한 tool을 호출하세요."
    )
