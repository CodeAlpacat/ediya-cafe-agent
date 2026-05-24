"""대화 처리 use-case.

`app.llm.agent.run_turn`이 LLM round-trip + dispatcher 호출까지 다 한다.
이 서비스는 비동기 환경에 wrap + **이번 턴에 발생한 tool_calls / cart events 추출**.
디버그 패널과 echo 메시지의 재료.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Tuple

from openai import OpenAI

from app.domain.cart import Cart
from app.llm.agent import AgentConfig, run_turn


async def process_message(
    client: OpenAI,
    message: str,
    cart: Cart,
    history: List[Dict[str, Any]],
    config: AgentConfig | None = None,
) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """사용자 메시지 1턴 처리.

    `run_turn`은 동기 + LLM 호출이 blocking → asyncio.to_thread로 격리.

    Returns:
        (final_reply_text, cart_snapshot, tool_calls_trace, new_cart_events)
        - tool_calls_trace: [{name, arguments, result}, ...] 이번 턴에 호출된 도구들
        - new_cart_events: 이번 턴에 새로 기록된 카트 이벤트 (대부분 도구 호출의 부수효과)
    """
    events_before = len(cart.history_snapshot())
    reply = await asyncio.to_thread(run_turn, client, message, cart, history, config)
    tool_calls = _extract_turn_tool_calls(history)
    new_events = cart.history_snapshot()[events_before:]
    return reply, cart.snapshot(), tool_calls, new_events


def _extract_turn_tool_calls(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """history에서 가장 최근 user 메시지 이후의 assistant tool_calls + tool 결과 추출.

    Returns: [{name, arguments, result}, ...]
    """
    # 가장 최근 user 메시지 위치 (없으면 빈 결과)
    last_user_idx = -1
    for i in range(len(history) - 1, -1, -1):
        if history[i].get("role") == "user":
            last_user_idx = i
            break
    if last_user_idx < 0:
        return []

    # tool_call_id → 도구 결과 dict
    results_by_id: Dict[str, Dict[str, Any]] = {}
    for m in history[last_user_idx + 1:]:
        if m.get("role") == "tool":
            tcid = m.get("tool_call_id") or ""
            try:
                results_by_id[tcid] = json.loads(m.get("content") or "{}")
            except json.JSONDecodeError:
                results_by_id[tcid] = {"_raw": m.get("content")}

    traces: List[Dict[str, Any]] = []
    for m in history[last_user_idx + 1:]:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                name = tc["function"]["name"]
                args_raw = tc["function"].get("arguments") or "{}"
                try:
                    args = json.loads(args_raw)
                except json.JSONDecodeError:
                    args = {"_raw": args_raw}
                traces.append({
                    "name": name,
                    "arguments": args,
                    "result": results_by_id.get(tc.get("id", ""), {}),
                })
    return traces
