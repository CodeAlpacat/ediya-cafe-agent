"""Ollama (OpenAI 호환) Gemma 4 E2B 클라이언트 wrapper.

핵심:
- chat_once: 단일 사용자 발화 → LLM 호출 1회 → tool_calls 추출 (Phase 2)
- run_turn:  멀티 round-trip (tool_calls → dispatcher → tool_result → 자연어 응답) (Phase 3)
- Silent Corruption Guard: 모델이 도구 호출 없이 "담았어요/빼드렸어요" 같은 confirmation 동사로
  응답하는 P0 케이스 감지 + 1회 retry. (QA stress test 발견 — F1/F2 silent corruption)
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from openai import OpenAI

from app.domain.cart import Cart
from app.dispatcher import dispatch_tool
from app.domain.menu import all_menu_names, detect_slang, keyword_menu_search
from app.llm.prompts import SYSTEM_PROMPT
from app.llm.tools import TOOLS

logger = logging.getLogger("ediya.agent")

# Silent corruption 감지용 confirmation 동사 어간.
# 도구 호출 없이 이 표현이 들어가면 모델이 "처리했다"고 말하지만 실제로는 안 한 상태.
_CONFIRMATION_VERB_STEMS = [
    "담았", "담을게", "담아드",
    "빼드", "빼겠",
    "변경했", "변경해드",
    "바꿨", "바꿔드", "바꾸어",
    "추가했", "추가됐", "추가해드",
    "제거했", "제거됐", "제거해드",
    "줄였", "줄여드",
    "주문 완료", "완료되었", "결제 도와",
]

_RETRY_HINT = (
    "[System hint] 직전 사용자 발화에는 명확한 주문/변경/제거/완료 의도가 있었어요. "
    "텍스트로 '처리했어요'라고 말하지 말고, 반드시 알맞은 tool을 호출해서 카트에 반영하세요. "
    "예: '담았어요' 라고 말하기 전에 add_menu tool을 호출. "
    "도구 호출 없이는 카트가 실제로 바뀌지 않아요."
)

# clarification followup 턴에서 메뉴가 확정됐는데도 '담을까요?'로 되묻는 패턴 감지용.
# 작은 모델은 메뉴가 다 정해져도 한 번 더 확인 질문을 하려는 습관이 있다.
_CONFIRM_QUESTION_STEMS = [
    "담을까", "담아드릴까", "담아 드릴까", "드릴까요",
    "추가할까", "주문할까", "해드릴까", "해 드릴까", "맞을까", "맞으실까",
]
_OPEN_QUESTION_WORDS = ["어떤", "중에서", "골라", "선택해", "무엇", "뭐로"]

_CONFIRM_RETRY_HINT = (
    "[System hint] 메뉴(온도+종류)가 이미 확정됐어요. 사용자에게 '담을까요?'라고 "
    "다시 묻지 마세요. 지금 바로 add_menu tool을 호출해서 카트에 담으세요. "
    "불필요한 재확인은 turn 낭비예요."
)


def _looks_confirm_question(text: str) -> bool:
    """메뉴가 정해진 상태에서 던지는 재확인 질문('~담을까요?')인지.

    '어떤 라떼?' 같은 열린 질문(아직 좁히는 중)은 제외 — 그건 정상 흐름.
    """
    has_confirm = any(s in text for s in _CONFIRM_QUESTION_STEMS)
    is_open = any(w in text for w in _OPEN_QUESTION_WORDS)
    return has_confirm and not is_open


# P4 referential pronoun + intent 패턴
_REFERENTIAL_WORDS = ["아까", "방금", "그거", "그것", "전에 시킨", "전에 주문", "전에 시킨 거", "이전에", "처음 시킨", "처음 주문"]
_INTENT_WORDS_FOR_REF = ["빼", "취소", "지워", "바꾸", "바꿔", "변경", "교체"]

# P6: 한 발화에 제거 + 추가가 함께 들어있는 복합 명령 패턴.
# 작은 모델은 이런 발화를 다음 턴으로 미루거나 통째로 되묻기만 한다.
_REMOVE_VERBS = ["빼", "취소", "지워", "제거", "줄여", "줄이"]
_ADD_VERBS = ["추가", "더 줘", "더 주", "더줘", "더주", "넣어", "담아"]


def _looks_silent_corruption(text: str) -> bool:
    """confirmation 동사가 있고 도구 호출이 없을 때 True."""
    return any(stem in text for stem in _CONFIRMATION_VERB_STEMS)


# silent-add 복구용 — 모델이 'X 담았어요'라고 말한 경우의 add 의도 확인 어간.
_ADD_CONFIRM_STEMS = ["담았", "담아드", "담을게", "추가했", "추가됐", "추가해드", "추가해 드"]
# 제거/교체 의도가 섞이면 함부로 add 복구를 하지 않는다.
_REMOVE_INTENT_MARKERS = ["빼", "취소", "제거", "줄여", "줄이", "바꿔", "변경", "교체"]
# 부정/실패 표현이 있으면 모델은 "담지 못했다"는 뜻 — 복구로 담으면 안 된다.
_NEGATION_MARKERS = [
    "못 담", "담지 못", "담을 수 없", "추가 못", "추가할 수 없",
    "없어요", "없습니다", "품절", "안 돼", "안돼", "불가",
]
_KO_QUANTITY = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6}


def _extract_quantity_from_text(text: str) -> int:
    """발화/응답 텍스트에서 'N잔' 수량 추출. 못 찾으면 1."""
    m = re.search(r"(\d+)\s*잔", text)
    if m:
        return max(1, int(m.group(1)))
    for word, n in _KO_QUANTITY.items():
        if f"{word} 잔" in text or f"{word}잔" in text:
            return n
    return 1


# 라떼 종류를 특정하는 수식어. 이게 없는 맨 '라떼'는 관용적으로 '카페라떼'.
_LATTE_QUALIFIERS = ["연유", "헤이즐넛", "바닐라", "녹차", "카라멜", "카페", "콜드브루", "디카페인"]


def _extract_menu_from_text(text: str) -> Optional[str]:
    """텍스트에서 정식 메뉴명을 추출. 느슨한 표현('아이스 라떼')도 해석.

    모델은 'X 담았어요'라고 할 때 메뉴를 띄어쓰거나 '카페'를 빠뜨려 부르는 경우가
    많다(예: '아이스 라떼' → 정식명 '아이스카페라떼'). 공백 제거 후 직접 매칭하고,
    수식어 없는 맨 '라떼'는 '카페라떼'로 보정해 재매칭한다.
    """
    despaced = text.replace(" ", "")
    hits = [kr for kr in all_menu_names() if kr in despaced]
    if hits:
        return max(hits, key=len)
    # 수식어 없는 '라떼' → '카페라떼' 보정 후 재매칭
    if "라떼" in despaced and not any(q in despaced for q in _LATTE_QUALIFIERS):
        expanded = despaced.replace("라떼", "카페라떼")
        hits = [kr for kr in all_menu_names() if kr in expanded]
        if hits:
            return max(hits, key=len)
    return None


def _recover_silent_add(text: str, cart: Cart) -> Optional[str]:
    """모델이 'X 담았어요'라고 해놓고 add_menu를 안 부른 경우의 마지막 복구.

    P0 guard의 retry까지 실패했을 때, 모델이 텍스트로 선언한 의도를 코드가
    대신 실행한다. 제거/교체 의도가 섞인 발화는 위험하므로 복구하지 않는다.
    """
    if any(mk in text for mk in _REMOVE_INTENT_MARKERS):
        return None
    if any(mk in text for mk in _NEGATION_MARKERS):
        return None  # "못 담았어요" 등 — 모델이 못 담았다는 뜻이므로 복구 금지
    if not any(s in text for s in _ADD_CONFIRM_STEMS):
        return None
    menu = _extract_menu_from_text(text)
    if not menu:
        return None
    qty = _extract_quantity_from_text(text)
    result = dispatch_tool("add_menu", {"menu": menu, "quantity": qty, "options": []}, cart)
    if result.get("status") in ("ADDED", "INCREMENTED"):
        logger.warning(
            "silent-add recovery: dispatched add_menu(%s, qty=%d) from model text", menu, qty
        )
        return f"{menu} {qty}잔 담았어요."
    return None


def _looks_referential_intent(user_text: str) -> bool:
    """'아까/방금/그거' + 의도 동사 패턴 감지."""
    has_ref = any(w in user_text for w in _REFERENTIAL_WORDS)
    has_intent = any(w in user_text for w in _INTENT_WORDS_FOR_REF)
    return has_ref and has_intent


def _looks_compound_action(user_text: str) -> bool:
    """한 발화에 제거 동작 + 추가 동작이 함께 들어있는지 감지 (P6)."""
    has_remove = any(w in user_text for w in _REMOVE_VERBS)
    has_add = any(w in user_text for w in _ADD_VERBS)
    return has_remove and has_add


def _build_compound_action_hint() -> str:
    """복합 명령 발화 감지 시 inject할 hint. 동작을 한 턴에 모두 처리하도록 유도."""
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


# 직전 점원 발화가 "메뉴를 좁히는 질문"임을 나타내는 신호.
# - "더 필요하세요?" 같은 마무리 질문: 신호 없음 → clarification 아님.
# - "아이스아메리카노 담았어요" 같은 확인 메시지: '아이스/핫'은 메뉴명 일부라
#   신호로 쓰면 오발동하므로 제외하고, 선택 유도 어휘만 신호로 쓴다.
_NARROWING_SIGNALS = ["어떤", "중에서", "골라", "아니면", "무엇으로", "뭐로"]


def _in_clarification_followup(history: List[Dict[str, Any]], user_text: str) -> bool:
    """직전 점원 발화가 메뉴를 좁히는 질문이었고, 이번 사용자 발화가 그 짧은 답인지 감지.

    이 경우 현재 발화 하나만 보고 만드는 Menu RAG hint는 노이즈가 된다.
    예: 점원이 "아이스/핫 중?" 묻고 사용자가 "아이스"라고 답하면, "아이스"로
    keyword 검색하면 전 메뉴가 쏟아져 모델이 대화 맥락 대신 그 목록을 따라간다.

    단, "더 필요하신 메뉴 있으세요?" 같은 마무리 질문 뒤의 짧은 새 주문까지
    오분류하지 않도록, 직전 발화에 실제 '좁히기' 신호가 있을 때만 True.
    """
    if len(user_text.strip()) > 7:  # 긴 발화는 새 주문일 가능성이 커서 제외
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


def _build_slang_hint(user_text: str) -> Optional[str]:
    """발화에 카페 줄임말('아아' 등)이 있으면 정식 메뉴를 명시하는 hint를 만든다.

    작은 모델은 줄임말을 모른 척 되묻는 경향이 있어, keyword 후보만으로는 부족하다.
    "'아아'='아이스아메리카노'"라고 못박아 바로 처리하게 한다.
    """
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


def _build_clarification_followup_hint() -> str:
    """clarification 답변 턴에 inject할 hint. 메뉴 목록 재나열 대신 맥락으로 좁히게."""
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


def _build_menu_context_hint(user_text: str) -> Optional[str]:
    """발화 관련 메뉴 후보를 키워드 검색으로 찾아 system hint로 inject.

    P2 hallucination 완화 — 모델이 menu list를 모르는 상태에서 '단팥빙수' 같은
    자유 발화를 '없어요'라고 거절하지 않게, 매장 메뉴 후보를 동적으로 inject.
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


def _build_post_inquiry_hint(history: List[Dict[str, Any]], user_text: str) -> Optional[str]:
    """Inquire 후 사용자가 specific 메뉴를 말한 패턴 감지 → add_menu 강제 hint.

    L3 처치(tool result 압축)만으로는 E2B가 inquiry 모드 stuck.
    Agent layer에서 직접 감지:
    - 직전 turn에 inquire_menu_info 호출됨
    - 현재 user 발화에서 정확한 메뉴 후보가 추출됨 (keyword_menu_search가 매칭)
    → "add_menu 호출하라. inquire 또 호출 금지" hint inject.
    """
    # 직전 assistant turn이 inquire_menu_info 호출했는지
    last_inquire = False
    for msg in reversed(history):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if tc["function"]["name"] == "inquire_menu_info":
                    last_inquire = True
            break
        if msg.get("role") == "user":
            break  # 더 거슬러 올라가는 건 의미 없음

    if not last_inquire:
        return None

    # 사용자 발화에서 메뉴 후보 매칭 (있어도 없어도 hint inject — 의도가 주문이라는 신호)
    candidates = keyword_menu_search(user_text, max_k=3)
    if not candidates:
        return None

    # 매칭된 후보는 참고용으로만 (사용자가 정확한 메뉴명을 말했을 수도, 다른 메뉴일 수도)
    candidate_names = ", ".join(c["kr"] for c in candidates)
    return (
        "[Post-inquiry routing] 사용자가 메뉴 안내를 받은 후 구체적인 메뉴를 명시했어요. "
        "이건 명확한 주문 의도예요. inquire_menu_info를 다시 호출하지 말고 add_menu를 호출하세요. "
        f"사용자 발화에서 추출한 메뉴 후보: {candidate_names}. "
        "이 후보 중 가장 적합한 것 또는 사용자가 정확히 말한 메뉴명으로 add_menu(menu=..., quantity=..., options=[]) 호출."
    )


def _format_cart_item(item: Dict[str, Any]) -> str:
    """카트 항목 1개를 '메뉴명 (옵션: a, b)' 형태 문자열로."""
    if item.get("options"):
        return f"{item['menu']} (옵션: {', '.join(item['options'])})"
    return item["menu"]


def _build_referential_hint(cart: Cart) -> Optional[str]:
    """사용자가 referential 표현 썼을 때 hint message 생성. 카트에 메뉴 있을 때만."""
    snap = cart.snapshot()
    if not snap:
        return None
    last = snap[-1]  # 가장 최근 추가된 항목
    menu_list = ", ".join(_format_cart_item(it) for it in snap)
    return (
        f"[Referential hint] 사용자가 '아까/방금/그거' 같은 표현으로 카트의 메뉴를 가리켰어요. "
        f"가장 최근에 추가된 메뉴: {_format_cart_item(last)}. "
        f"전체 카트: {menu_list}. "
        f"이 정보로 적절한 tool을 호출하세요."
    )


@dataclass
class AgentConfig:
    model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "gemma4:e2b"))
    base_url: str = field(
        default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    )
    api_key: str = "ollama"
    temperature: float = 0.0
    seed: int = 4242
    max_round_trips: int = 5
    silent_corruption_guard: bool = True  # P0 guard 활성화 여부


def make_client(config: Optional[AgentConfig] = None) -> OpenAI:
    cfg = config or AgentConfig()
    return OpenAI(base_url=cfg.base_url, api_key=cfg.api_key)


def chat_once(
    client: OpenAI,
    user_message: str,
    config: Optional[AgentConfig] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    tool_choice: str = "auto",
) -> Dict[str, Any]:
    """단일턴: LLM 호출 1회. tool_calls 또는 텍스트 응답 반환.

    Args:
        tool_choice: "auto" (모델이 결정) 또는 "required" (반드시 도구 호출).
                     단일턴 테스트에서 의도가 명확한 발화는 "required"로 호출 강제 가능.

    Returns:
        {
            "tool_calls": [{"name": ..., "arguments": {...}, "id": ...}, ...],
            "response_text": str,
            "finish_reason": str,
            "raw_message": ChatCompletionMessage,
        }
    """
    cfg = config or AgentConfig()
    messages: List[Dict[str, Any]] = []
    if history:
        messages.extend(history)
    else:
        messages.append({"role": "system", "content": SYSTEM_PROMPT})
    messages.append({"role": "user", "content": user_message})

    resp = client.chat.completions.create(
        model=cfg.model,
        messages=messages,
        tools=TOOLS,
        tool_choice=tool_choice,
        temperature=cfg.temperature,
        seed=cfg.seed,
        parallel_tool_calls=True,  # P5: 복수 메뉴 동시 호출 허용
    )
    msg = resp.choices[0].message
    finish = resp.choices[0].finish_reason

    tool_calls: List[Dict[str, Any]] = []
    if msg.tool_calls:
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append({"id": tc.id, "name": tc.function.name, "arguments": args})

    return {
        "tool_calls": tool_calls,
        "response_text": msg.content or "",
        "finish_reason": finish,
        "raw_message": msg,
    }


def _is_stuck_loop(history: List[Dict[str, Any]], window: int = 3) -> bool:
    """history의 가장 최근 assistant tool_calls가 동일 (name, arguments)로 N회 반복인지.

    P3 stuck loop 감지 — 모델이 이미 처리된/유효하지 않은 도구 호출을 stale state로 반복.
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


def run_turn(
    client: OpenAI,
    user_message: str,
    cart: Cart,
    history: List[Dict[str, Any]],
    config: Optional[AgentConfig] = None,
) -> str:
    """멀티 round-trip: tool_calls → dispatcher → tool result → 자연어 응답 반복.

    history는 in-place로 갱신됨 (system + user/assistant/tool 메시지 누적).
    최종 자연어 응답 텍스트를 반환.

    Guards:
    - Silent Corruption Guard: 도구 호출 0건 + confirmation 동사 응답 → 1회 retry.
    - Stuck Loop Detection: 동일 tool_call (name+args) 3회 반복 시 break + fallback.
    """
    cfg = config or AgentConfig()

    if not history:
        history.append({"role": "system", "content": SYSTEM_PROMPT})
    else:
        # 이전 턴에 주입된 1회성 system 힌트(Menu RAG / Clarification / retry 등)를 제거한다.
        # history에 영구 누적되면 낡은 힌트가 이후 턴의 맥락을 오염시킨다.
        # history[0] = SYSTEM_PROMPT 는 유지, 그 외 system 메시지는 모두 제거.
        history[:] = [history[0]] + [m for m in history[1:] if m.get("role") != "system"]

    # 줄임말 hint — '아아' 같은 표현을 정식 메뉴로 못박아 되묻기를 방지 (followup 여부 무관)
    slang_hint = _build_slang_hint(user_message)
    if slang_hint:
        history.append({"role": "system", "content": slang_hint})

    # clarification 답변 턴이면 Menu RAG hint 대신 맥락 좁히기 hint를 inject.
    # 짧은 답("아이스" 등)으로 keyword 검색하면 전 메뉴가 쏟아져 맥락을 덮어쓰기 때문.
    in_followup = _in_clarification_followup(history, user_message)
    if in_followup:
        history.append({"role": "system", "content": _build_clarification_followup_hint()})
    else:
        # 키워드 메뉴 매칭: 발화 관련 메뉴 후보를 system hint로 inject (P2 hallucination 완화)
        menu_hint = _build_menu_context_hint(user_message)
        if menu_hint:
            history.append({"role": "system", "content": menu_hint})

    # P6: 복합 명령(제거 + 추가) 감지 시 한 턴 처리 hint inject
    if _looks_compound_action(user_message):
        history.append({"role": "system", "content": _build_compound_action_hint()})

    # Post-inquiry routing: inquire 후 메뉴 명시 발화면 add_menu force hint
    post_inq_hint = _build_post_inquiry_hint(history, user_message)
    if post_inq_hint:
        history.append({"role": "system", "content": post_inq_hint})

    # P4: referential pronoun + intent 감지 시 hint inject
    if _looks_referential_intent(user_message):
        hint = _build_referential_hint(cart)
        if hint:
            history.append({"role": "system", "content": hint})

    history.append({"role": "user", "content": user_message})

    tool_calls_in_turn = 0
    guard_used = False
    confirm_guard_used = False

    for _ in range(cfg.max_round_trips):
        resp = client.chat.completions.create(
            model=cfg.model,
            messages=history,
            tools=TOOLS,
            tool_choice="auto",
            temperature=cfg.temperature,
            seed=cfg.seed,
            parallel_tool_calls=True,  # P5: 한 발화에 복수 메뉴 동시 처리 허용
        )
        msg = resp.choices[0].message
        finish = resp.choices[0].finish_reason

        if msg.tool_calls:
            tool_calls_in_turn += len(msg.tool_calls)
            history.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
            )
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = dispatch_tool(tc.function.name, args, cart)
                history.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

            # P3 stuck loop 감지 — 동일 도구 호출 반복 시 break
            if _is_stuck_loop(history, window=3):
                logger.warning("stuck loop detected (same tool_call 3 times) — breaking")
                fallback = (
                    "주문 처리에 잠시 문제가 있었어요. "
                    "원하시는 메뉴와 수량을 다시 한 번 말씀해 주시겠어요?"
                )
                history.append({"role": "assistant", "content": fallback})
                return fallback
            continue

        # final text 응답
        text = msg.content or ""

        # P0 Silent Corruption Guard
        if (
            cfg.silent_corruption_guard
            and not guard_used
            and tool_calls_in_turn == 0
            and _looks_silent_corruption(text)
        ):
            logger.warning(
                "silent corruption detected — confirmation verb without tool call. retrying once."
            )
            guard_used = True
            # system hint를 history에 inject — 다음 round-trip에서 도구 호출 유도
            history.append({"role": "system", "content": _RETRY_HINT})
            continue

        # Clarification 재확인 루프 가드 — 메뉴가 확정됐는데 '담을까요?'로 되물을 때
        if (
            in_followup
            and not confirm_guard_used
            and tool_calls_in_turn == 0
            and _looks_confirm_question(text)
        ):
            logger.warning(
                "confirm-question loop detected in clarification followup. retrying once."
            )
            confirm_guard_used = True
            history.append({"role": "system", "content": _CONFIRM_RETRY_HINT})
            continue

        history.append({"role": "assistant", "content": text})
        if finish in ("stop", "length"):
            # guard가 발동했지만 retry도 silent였다면:
            # 먼저 모델이 텍스트로 선언한 add 의도를 코드가 대신 실행해 복구를 시도.
            if guard_used and tool_calls_in_turn == 0:
                recovered = _recover_silent_add(text, cart)
                if recovered:
                    history[-1] = {"role": "assistant", "content": recovered}
                    return recovered
                # 복구도 불가하면 사용자에게 명시적 확인 요청
                fallback = (
                    "한 번 더 정확히 확인하고 싶어요. "
                    "주문하실 메뉴와 수량을 다시 말씀해 주시겠어요?"
                )
                # 마지막 assistant 메시지를 fallback으로 교체
                history[-1] = {"role": "assistant", "content": fallback}
                return fallback
            return text

    # round-trip 한도 초과 — 디버그 문자열 대신 손님에게 자연스러운 안내
    fallback = (
        "주문 처리가 길어지고 있어요. 원하시는 메뉴와 수량을 한 번만 더 "
        "정확히 말씀해 주시겠어요?"
    )
    history.append({"role": "assistant", "content": fallback})
    return fallback
