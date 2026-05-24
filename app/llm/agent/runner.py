"""에이전트 본 루프 — LLM-first 설계.

원칙 (PLAN_llm_first):
- 메뉴 데이터는 코드에 박지 않는다. 매 턴 [메뉴 후보/옵션 사전/매장 미보유]
  컨텍스트를 system msg로 dynamic inject — LLM이 단일 진실 소스로 사용.
- 슬랭/도메인 룰은 system_prompt에 자연어로 명시. 패턴매칭 사전 X.
- 가드는 stuck_loop만 유지 — 다른 가드 누적은 LLM 신뢰를 깎음.
- second-call(자연어 응답)은 LLM 강점 영역, 그대로 둠.
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
    TOOL_HALLUCINATION_RETRY_HINT,
    is_stuck_loop,
    looks_tool_hallucination,
)
from app.llm.menu_context import build_menu_context
from app.llm.prompts import SYSTEM_PROMPT
from app.llm.tools import TOOLS

logger = logging.getLogger("ediya.agent")


@dataclass
class AgentConfig:
    model: str = field(default_factory=lambda: get_settings().ollama_model)
    base_url: str = field(default_factory=lambda: get_settings().ollama_base_url)
    api_key: str = field(default_factory=lambda: get_settings().ollama_api_key)
    temperature: float = field(default_factory=lambda: get_settings().agent_temperature)
    seed: int = field(default_factory=lambda: get_settings().agent_seed)
    max_round_trips: int = field(
        default_factory=lambda: get_settings().agent_max_round_trips
    )


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
    """단일턴 LLM 호출 — 테스트/디버그 용. dynamic menu inject 미적용."""
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
        parallel_tool_calls=True,
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
    """직전 턴의 dynamic menu context system msg 제거. SYSTEM_PROMPT(index 0)는 유지.

    메뉴 컨텍스트는 발화마다 새로 만든다. 누적되면 stale 데이터로 LLM 혼란.
    """
    if not history:
        history.append({"role": "system", "content": SYSTEM_PROMPT})
        return
    history[:] = [history[0]] + [m for m in history[1:] if m.get("role") != "system"]


def _dispatch_tool_calls(
    msg_tool_calls, history: List[Dict[str, Any]], cart: Cart
) -> int:
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
    """LLM-first 흐름: 메뉴 컨텍스트 inject → LLM ↔ dispatcher round-trip.

    history는 in-place 갱신. 최종 자연어 응답 텍스트 반환.

    Safety:
    - stuck loop: 동일 tool_call 3회 반복 시 break + fallback.
    - round trip 한도: max_round_trips 초과 시 fallback.
    """
    cfg = config or AgentConfig()

    _prepare_history(history)

    # LLM-first 핵심: 메뉴 데이터를 system msg로 dynamic inject.
    # 발화마다 관련 후보만 + 옵션 사전 + 매장 미보유. 코드 변경 0.
    menu_ctx = build_menu_context(user_message)
    history.append({"role": "system", "content": menu_ctx})

    history.append({"role": "user", "content": user_message})

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
            _dispatch_tool_calls(msg.tool_calls, history, cart)
            if is_stuck_loop(history, window=3):
                logger.warning("stuck loop detected — breaking")
                history.append({"role": "assistant", "content": _STUCK_LOOP_FALLBACK})
                return _STUCK_LOOP_FALLBACK
            continue

        text = msg.content or ""

        # tool hallucination — 모델이 ```json / add_menu(...) 텍스트로 출력. 1회 retry.
        if not hallucination_guard_used and looks_tool_hallucination(text):
            logger.warning("tool hallucination — code/JSON text instead of API. retry once.")
            hallucination_guard_used = True
            history.append(
                {"role": "system", "content": TOOL_HALLUCINATION_RETRY_HINT}
            )
            continue

        history.append({"role": "assistant", "content": text})
        if finish in ("stop", "length"):
            return text

    history.append({"role": "assistant", "content": _ROUND_TRIP_LIMIT_FALLBACK})
    return _ROUND_TRIP_LIMIT_FALLBACK
