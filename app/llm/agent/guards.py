"""모델 출력 형식 안전망 (LLM-first 보완).

LLM-first 설계의 가드 누적은 회피지만, 작은 모델의 *출력 형식 일탈*은
결정론적으로 검출 가능하고 가드가 정당하다 (룰 6 자기검토 통과):
- stuck loop: 무한 round-trip 방지
- tool hallucination: ```json / add_menu(...) 텍스트 등 도구 호출 흉내 — 1회 retry

silent corruption / silent-add recovery / confirm-question 같은 *의미 추론* 가드는
여전히 LLM-first 정신 위배 — 모델 자유도 침범. 추가 X.
"""
from __future__ import annotations

import re
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


# 모델이 OpenAI tool-calling protocol 대신 *텍스트로 호출 흉내*낸 신호.
# E2B 특유 실패 모드 — ```json, add_menu(...), "tool_calls": ... 등.
_HALLUCINATION_PATTERNS = [
    re.compile(r"```\s*(json|python|tool|function)?", re.IGNORECASE),
    re.compile(r'"tool_calls"\s*:'),
    re.compile(r'"function"\s*:\s*"'),
    re.compile(r"\btool_name\b\s*[:=]"),
    re.compile(r"\b(add_menu|remove_menu|replace_menu|change_option|inquire_menu_info|done|undo|add_item|update_order)\s*\("),
]

TOOL_HALLUCINATION_RETRY_HINT = (
    "[System hint] 직전 응답에 ```json 코드블록이나 add_menu(...) 같은 함수 호출 "
    "텍스트를 출력했어요. 그건 진짜 도구 호출이 아니라 텍스트라 카트가 안 바뀝니다. "
    "도구를 부르려면 OpenAI tool API로 실제 호출해주세요. "
    "사용자에게는 자연스러운 한국어 응답만 보여주세요."
)


def looks_tool_hallucination(text: str) -> bool:
    """모델이 도구 호출 protocol을 텍스트로 흉내내는지 (코드블록/JSON/함수 호출 텍스트)."""
    return any(p.search(text) for p in _HALLUCINATION_PATTERNS)
