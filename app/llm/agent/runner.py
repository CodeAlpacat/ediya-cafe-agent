"""에이전트 본 루프: NLU 사전처리 → LLM round-trip → dispatcher.

설계 원칙 (PLAN_nlu_refactor):
- NLU(`app.nlu`)가 결정론 영역을 모두 처리한다 (슬랭/온도/디카페인/옵션 매핑).
- 모델에게는 IntentProposal 1개를 system message로 inject. 누적 hint 금지.
- 모델 자유도가 좁아져서 가드 누적 불필요 (stuck_loop만 안전망으로 유지).
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
from app.llm.agent.guards import is_stuck_loop
from app.llm.agent.proposal_renderer import render_proposal
from app.llm.prompts import SYSTEM_PROMPT
from app.llm.tools import TOOLS
from app.nlu import extract_intent

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
    """단일턴 LLM 호출 1회. 테스트/디버그 용."""
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
    """직전 턴에 inject된 NLU system 메시지를 제거. SYSTEM_PROMPT(index 0)는 유지.

    NLU 분석은 발화마다 새로 만든다. 이전 턴의 분석이 남아있으면 컨텍스트 오염.
    """
    if not history:
        history.append({"role": "system", "content": SYSTEM_PROMPT})
        return
    history[:] = [history[0]] + [m for m in history[1:] if m.get("role") != "system"]


def _dispatch_tool_calls(
    msg_tool_calls, history: List[Dict[str, Any]], cart: Cart
) -> int:
    """assistant tool_calls 메시지 + 각 도구 결과를 history에 append."""
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
    """멀티 round-trip: NLU → IntentProposal inject → LLM ↔ dispatcher.

    history는 in-place로 갱신됨. 최종 자연어 응답 텍스트 반환.

    Safety:
    - stuck loop: 동일 tool_call 3회 반복 시 break + fallback.
    - round trip 한도: max_round_trips 초과 시 fallback.
    """
    cfg = config or AgentConfig()

    _prepare_history(history)

    # NLU 사전처리 — cart 상태를 넘겨 change_option↔add_menu 결정에 반영
    cart_menus = [it["menu"] for it in cart.snapshot()]
    proposal = extract_intent(user_message, cart_menus=cart_menus)
    proposal_msg = render_proposal(proposal)
    history.append({"role": "system", "content": proposal_msg})
    logger.debug("NLU proposal: action=%s intents=%d", proposal.action, len(proposal.intents))

    history.append({"role": "user", "content": user_message})

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

        # 최종 자연어 응답
        text = msg.content or ""
        history.append({"role": "assistant", "content": text})
        if finish in ("stop", "length"):
            return text

    history.append({"role": "assistant", "content": _ROUND_TRIP_LIMIT_FALLBACK})
    return _ROUND_TRIP_LIMIT_FALLBACK
