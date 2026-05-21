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
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from openai import OpenAI

from app.cart import Cart
from app.dispatcher import dispatch_tool
from app.menu import search_menus_for_utterance
from app.prompts import SYSTEM_PROMPT
from app.tools import TOOLS

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

# P4 referential pronoun + intent 패턴
_REFERENTIAL_WORDS = ["아까", "방금", "그거", "그것", "전에 시킨", "전에 주문", "전에 시킨 거", "이전에", "처음 시킨", "처음 주문"]
_INTENT_WORDS_FOR_REF = ["빼", "취소", "지워", "바꾸", "바꿔", "변경", "교체"]


def _looks_silent_corruption(text: str) -> bool:
    """confirmation 동사가 있고 도구 호출이 없을 때 True."""
    return any(stem in text for stem in _CONFIRMATION_VERB_STEMS)


def _looks_referential_intent(user_text: str) -> bool:
    """'아까/방금/그거' + 의도 동사 패턴 감지."""
    has_ref = any(w in user_text for w in _REFERENTIAL_WORDS)
    has_intent = any(w in user_text for w in _INTENT_WORDS_FOR_REF)
    return has_ref and has_intent


def _build_menu_context_hint(user_text: str) -> Optional[str]:
    """RAG Step 1: 발화 관련 메뉴 후보를 system hint로 inject.

    P2 hallucination 완화 — 모델이 menu list를 모르는 상태에서 '단팥빙수' 같은
    자유 발화를 '없어요'라고 거절하지 않게, 매장 메뉴 후보를 동적으로 inject.
    """
    candidates = search_menus_for_utterance(user_text, max_k=5)
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
        + "\n위 메뉴는 매장에 분명히 존재해요. '없어요'라고 거절하지 말고, "
        + "발화 의도에 맞는 메뉴를 정확한 이름으로 도구 호출하거나 사용자에게 종류를 되물어보세요."
    )


def _build_post_inquiry_hint(history: List[Dict[str, Any]], user_text: str) -> Optional[str]:
    """Inquire 후 사용자가 specific 메뉴를 말한 패턴 감지 → add_menu 강제 hint.

    L3 처치(tool result 압축)만으로는 E2B가 inquiry 모드 stuck.
    Agent layer에서 직접 감지:
    - 직전 turn에 inquire_menu_info 호출됨
    - 현재 user 발화에서 정확한 메뉴 후보가 추출됨 (search_menus가 매칭)
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
    candidates = search_menus_for_utterance(user_text, max_k=3)
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


def _build_referential_hint(cart: Cart) -> Optional[str]:
    """사용자가 referential 표현 썼을 때 hint message 생성. 카트에 메뉴 있을 때만."""
    snap = cart.snapshot()
    if not snap:
        return None
    # 가장 최근 추가된 항목 (snap의 마지막)
    last = snap[-1]
    options_str = f" (옵션: {', '.join(last['options'])})" if last["options"] else ""
    menu_list = ", ".join(f"{it['menu']}{f' (옵션: {chr(44).join(it[chr(34)+chr(111)+chr(112)+chr(116)+chr(105)+chr(111)+chr(110)+chr(115)+chr(34)])})' if it['options'] else ''}" for it in snap)
    return (
        f"[Referential hint] 사용자가 '아까/방금/그거' 같은 표현으로 카트의 메뉴를 가리켰어요. "
        f"가장 최근에 추가된 메뉴: {last['menu']}{options_str}. "
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

    # RAG Step 1: 발화 관련 메뉴 후보를 system hint로 inject (P2 hallucination 완화)
    menu_hint = _build_menu_context_hint(user_message)
    if menu_hint:
        history.append({"role": "system", "content": menu_hint})

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

        history.append({"role": "assistant", "content": text})
        if finish in ("stop", "length"):
            # guard가 발동했지만 retry도 silent였다면 사용자에게 명시적 확인 요청
            if guard_used and tool_calls_in_turn == 0:
                fallback = (
                    "한 번 더 정확히 확인하고 싶어요. "
                    "주문하실 메뉴와 수량을 다시 말씀해 주시겠어요?"
                )
                # 마지막 assistant 메시지를 fallback으로 교체
                history[-1] = {"role": "assistant", "content": fallback}
                return fallback
            return text

    return "(max round trips reached)"
