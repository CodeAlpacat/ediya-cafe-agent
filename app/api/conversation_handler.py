"""대화 처리 어댑터.

`app.llm.agent.run_turn`이 이미 first-call → tool dispatch → second-call 전체를
처리하므로, 이 모듈은 비동기 환경에서 호출하기 좋게 wrap만 한다.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Tuple

from app.llm.agent import AgentConfig, run_turn
from app.domain.cart import Cart

from . import openai_client

logger = logging.getLogger("ediya")


async def process_message(
    message: str,
    cart: Cart,
    history: List[Dict[str, Any]],
    config: AgentConfig | None = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """사용자 메시지 1턴 처리.

    `run_turn`은 동기 함수 + LLM 호출이 blocking → asyncio.to_thread로 격리.

    Returns:
        (final_reply_text, cart_snapshot)
    """
    if openai_client is None:
        raise RuntimeError("Ollama 클라이언트가 초기화되지 않았습니다.")

    reply = await asyncio.to_thread(
        run_turn, openai_client, message, cart, history, config
    )
    return reply, cart.snapshot()
