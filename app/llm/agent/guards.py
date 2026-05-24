"""모델 라우팅 안전망 — stuck loop 1종만 유지.

이전에는 silent corruption / tool hallucination / confirm-question / silent-add recovery
등 4종 가드가 있었으나, NLU 사전처리(`app.nlu`) 도입으로 모델 자유도가 좁아지면서
실효가 사라져 제거함 (PLAN_nlu_refactor Phase 3).

stuck loop만 무한 round-trip 방지용으로 유지한다.
"""
from __future__ import annotations

from typing import Any, Dict, List


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
