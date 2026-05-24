"""에이전트 본 루프: 멀티 round-trip + 가드.

run_turn 책임:
1. 직전 턴의 1회성 system hint 제거 (누적 오염 방지)
2. 발화 분석 → hint 주입 (slang/RAG/clarification/post-inquiry/compound/referential)
3. LLM ↔ dispatcher round-trip (max_round_trips 회)
4. 가드 적용: silent corruption, confirm-question, stuck loop
5. 최후 수단: silent-add 복구 또는 안내 fallback
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from openai import OpenAI

from app.core.config import get_settings
from app.dispatcher import dispatch_tool
from app.domain.cart import Cart
from app.llm.agent.guards import (
    CONFIRM_RETRY_HINT,
    SILENT_CORRUPTION_RETRY_HINT,
    TOOL_HALLUCINATION_RETRY_HINT,
    is_stuck_loop,
    looks_confirm_question,
    looks_silent_corruption,
    looks_tool_hallucination,
    recover_silent_add,
)
from app.llm.agent.hints import (
    build_clarification_followup_hint,
    build_compound_action_hint,
    build_menu_context_hint,
    build_post_inquiry_hint,
    build_referential_hint,
    build_slang_hint,
    in_clarification_followup,
    looks_compound_action,
    looks_referential_intent,
)
from app.llm.prompts import SYSTEM_PROMPT
from app.llm.tools import TOOLS

logger = logging.getLogger("ediya.agent")


@dataclass
class AgentConfig:
    """run_turn 동작 파라미터. 기본값은 Settings에서 가져온다."""

    model: str = field(default_factory=lambda: get_settings().ollama_model)
    base_url: str = field(default_factory=lambda: get_settings().ollama_base_url)
    api_key: str = field(default_factory=lambda: get_settings().ollama_api_key)
    temperature: float = field(default_factory=lambda: get_settings().agent_temperature)
    seed: int = field(default_factory=lambda: get_settings().agent_seed)
    max_round_trips: int = field(
        default_factory=lambda: get_settings().agent_max_round_trips
    )
    silent_corruption_guard: bool = field(
        default_factory=lambda: get_settings().agent_silent_corruption_guard
    )


def make_client(config: Optional[AgentConfig] = None) -> OpenAI:
    """편의 팩토리. AgentConfig 기반 OpenAI 호환 클라이언트."""
    cfg = config or AgentConfig()
    return OpenAI(base_url=cfg.base_url, api_key=cfg.api_key)


def chat_once(
    client: OpenAI,
    user_message: str,
    config: Optional[AgentConfig] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    tool_choice: str = "auto",
) -> Dict[str, Any]:
    """단일턴 LLM 호출 1회. tool_calls 또는 텍스트 응답 반환.

    Args:
        tool_choice: "auto" (모델이 결정) 또는 "required" (반드시 도구 호출).
                     단일턴 테스트에서 의도가 명확한 발화는 "required"로 강제 가능.

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
            tool_calls.append(
                {"id": tc.id, "name": tc.function.name, "arguments": args}
            )

    return {
        "tool_calls": tool_calls,
        "response_text": msg.content or "",
        "finish_reason": finish,
        "raw_message": msg,
    }


def _prepare_history(history: List[Dict[str, Any]]) -> None:
    """직전 턴에 주입된 1회성 system 힌트를 제거. history[0](SYSTEM_PROMPT)는 유지.

    히스토리에 영구 누적되면 낡은 hint가 이후 턴의 맥락을 오염시킨다.
    """
    if not history:
        history.append({"role": "system", "content": SYSTEM_PROMPT})
        return
    history[:] = [history[0]] + [m for m in history[1:] if m.get("role") != "system"]


def _inject_hints(
    history: List[Dict[str, Any]], user_message: str, cart: Cart
) -> bool:
    """발화 분석 후 적절한 system hint들을 history에 inject.

    Returns:
        in_followup — clarification followup 여부 (run_turn 가드 활성화 조건에 쓰임).
    """
    # 줄임말 — '아아' 같은 표현을 정식 메뉴로 못박아 되묻기 방지 (followup 무관)
    slang_hint = build_slang_hint(user_message)
    if slang_hint:
        history.append({"role": "system", "content": slang_hint})

    # clarification 답변 턴이면 Menu RAG hint 대신 맥락 좁히기 hint를 inject.
    in_followup = in_clarification_followup(history, user_message)
    if in_followup:
        history.append(
            {"role": "system", "content": build_clarification_followup_hint()}
        )
    else:
        menu_hint = build_menu_context_hint(user_message)
        if menu_hint:
            history.append({"role": "system", "content": menu_hint})

    # P6: 복합 명령
    if looks_compound_action(user_message):
        history.append({"role": "system", "content": build_compound_action_hint()})

    # Post-inquiry routing
    post_inq_hint = build_post_inquiry_hint(history, user_message)
    if post_inq_hint:
        history.append({"role": "system", "content": post_inq_hint})

    # P4: referential pronoun
    if looks_referential_intent(user_message):
        ref_hint = build_referential_hint(cart)
        if ref_hint:
            history.append({"role": "system", "content": ref_hint})

    return in_followup


def _dispatch_tool_calls(
    msg_tool_calls, history: List[Dict[str, Any]], cart: Cart
) -> int:
    """assistant tool_calls 메시지 + 각 도구 결과를 history에 append.

    Returns 호출한 tool 수.
    """
    history.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg_tool_calls
            ],
        }
    )
    for tc in msg_tool_calls:
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
    return len(msg_tool_calls)


_STUCK_LOOP_FALLBACK = (
    "주문 처리에 잠시 문제가 있었어요. "
    "원하시는 메뉴와 수량을 다시 한 번 말씀해 주시겠어요?"
)
_RECOVERY_FALLBACK = (
    "한 번 더 정확히 확인하고 싶어요. "
    "주문하실 메뉴와 수량을 다시 말씀해 주시겠어요?"
)
_ROUND_TRIP_LIMIT_FALLBACK = (
    "주문 처리가 길어지고 있어요. 원하시는 메뉴와 수량을 한 번만 더 "
    "정확히 말씀해 주시겠어요?"
)


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
    - Silent Corruption: 도구 호출 0건 + confirmation 동사 응답 → 1회 retry.
    - Confirm-question loop (clarification followup에서만 활성): 메뉴 확정 후 재확인 질문.
    - Stuck Loop: 동일 tool_call (name+args) 3회 반복 시 break + fallback.
    - Recovery: silent corruption retry까지 실패 시 모델 텍스트로 add 의도 복구 시도.
    """
    cfg = config or AgentConfig()

    _prepare_history(history)
    in_followup = _inject_hints(history, user_message, cart)
    history.append({"role": "user", "content": user_message})

    tool_calls_in_turn = 0
    silent_guard_used = False
    confirm_guard_used = False
    hallucination_guard_used = False

    for _ in range(cfg.max_round_trips):
        resp = client.chat.completions.create(
            model=cfg.model,
            messages=history,
            tools=TOOLS,
            tool_choice="auto",
            temperature=cfg.temperature,
            seed=cfg.seed,
            parallel_tool_calls=True,
        )
        msg = resp.choices[0].message
        finish = resp.choices[0].finish_reason

        if msg.tool_calls:
            tool_calls_in_turn += _dispatch_tool_calls(msg.tool_calls, history, cart)
            if is_stuck_loop(history, window=3):
                logger.warning("stuck loop detected (same tool_call 3 times) — breaking")
                history.append({"role": "assistant", "content": _STUCK_LOOP_FALLBACK})
                return _STUCK_LOOP_FALLBACK
            continue

        # final text 응답
        text = msg.content or ""

        # P7 tool-call hallucination — 코드블록 / 가짜 함수 호출 텍스트
        if (
            not hallucination_guard_used
            and looks_tool_hallucination(text)
        ):
            logger.warning(
                "tool-call hallucination detected — model emitted code/function text instead of real tool. retrying once."
            )
            hallucination_guard_used = True
            history.append({"role": "system", "content": TOOL_HALLUCINATION_RETRY_HINT})
            continue

        # P0 silent corruption
        if (
            cfg.silent_corruption_guard
            and not silent_guard_used
            and tool_calls_in_turn == 0
            and looks_silent_corruption(text)
        ):
            logger.warning(
                "silent corruption detected — confirmation verb without tool call. retrying once."
            )
            silent_guard_used = True
            history.append(
                {"role": "system", "content": SILENT_CORRUPTION_RETRY_HINT}
            )
            continue

        # confirm-question loop (clarification followup 한정)
        if (
            in_followup
            and not confirm_guard_used
            and tool_calls_in_turn == 0
            and looks_confirm_question(text)
        ):
            logger.warning(
                "confirm-question loop detected in clarification followup. retrying once."
            )
            confirm_guard_used = True
            history.append({"role": "system", "content": CONFIRM_RETRY_HINT})
            continue

        history.append({"role": "assistant", "content": text})
        if finish in ("stop", "length"):
            # silent guard 발동 후 retry도 silent였다면:
            #   먼저 모델이 텍스트로 선언한 add 의도를 코드가 대신 dispatch.
            if silent_guard_used and tool_calls_in_turn == 0:
                recovered = recover_silent_add(text, cart)
                if recovered:
                    history[-1] = {"role": "assistant", "content": recovered}
                    return recovered
                history[-1] = {"role": "assistant", "content": _RECOVERY_FALLBACK}
                return _RECOVERY_FALLBACK
            return text

    # round-trip 한도 초과
    history.append({"role": "assistant", "content": _ROUND_TRIP_LIMIT_FALLBACK})
    return _ROUND_TRIP_LIMIT_FALLBACK
