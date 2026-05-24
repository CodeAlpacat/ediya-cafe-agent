"""POST /chat — 사용자 메시지 1턴 처리."""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException

from app.api.deps import OpenAIClientDep, SessionStoreDep
from app.api.schemas import (
    CartEventDTO,
    CartLine,
    ChatResponse,
    MessageRequest,
    ToolCallTrace,
)
from app.services.cart_pricing import enrich_with_total
from app.services.chat_service import process_message

logger = logging.getLogger("ediya")

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: MessageRequest,
    store: SessionStoreDep,
    client: OpenAIClientDep,
) -> ChatResponse:
    """session_id 없으면 새로 발급. per-session lock으로 동시 요청 직렬화."""
    session_id = request.session_id or f"session_{uuid.uuid4().hex[:12]}"
    state = store.get_or_create(session_id)

    logger.info("[CHAT] session=%s message=%s", session_id, request.message[:80])

    async with state.lock:
        try:
            reply, cart_snapshot, tool_calls, new_events = await process_message(
                client=client,
                message=request.message,
                cart=state.cart,
                history=state.history,
            )
        except Exception as exc:  # pragma: no cover — 로깅 후 500
            logger.error("chat 처리 오류: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc))

    cart, total = enrich_with_total(cart_snapshot)
    return ChatResponse(
        reply=reply,
        session_id=session_id,
        cart=[CartLine(**c) for c in cart],
        total=total,
        tool_calls=[ToolCallTrace(**tc) for tc in tool_calls],
        cart_events=[
            CartEventDTO(action=e["action"], summary=e["summary"], timestamp=e["timestamp"])
            for e in new_events
        ],
    )
