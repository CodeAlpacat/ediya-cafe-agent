"""API 요청/응답 Pydantic 모델."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MessageRequest(BaseModel):
    """채팅 메시지 요청."""

    message: str
    session_id: Optional[str] = None


class SessionRequest(BaseModel):
    """세션 초기화 요청. session_id 없으면 전체 초기화."""

    session_id: Optional[str] = None


class CartItem(BaseModel):
    menu: str
    quantity: int
    options: List[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    cart: List[CartItem]


class HealthResponse(BaseModel):
    status: str
    service: str = "ediya-cafe-agent"
