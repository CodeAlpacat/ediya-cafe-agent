"""HTTP 경계 Pydantic 스키마. 라우터에서 `response_model=`로 부착해 응답 계약을 박는다."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ---------- 요청 ----------

class MessageRequest(BaseModel):
    """채팅 메시지 요청. session_id 없으면 서버가 새로 발급."""

    message: str = Field(..., min_length=1)
    session_id: Optional[str] = None


class SessionRequest(BaseModel):
    """세션 초기화 요청. session_id 없으면 전체 초기화."""

    session_id: Optional[str] = None


class CartActionRequest(BaseModel):
    """Visual UI에서 직접 dispatcher를 호출 — LLM 우회.

    `action`은 dispatcher 핸들러 이름과 동일 (add_menu / remove_menu /
    change_option / replace_menu / undo). args는 해당 핸들러가 받는 인자.
    """

    session_id: str
    action: Literal[
        "add_menu", "remove_menu", "change_option", "replace_menu", "undo",
    ]
    args: Dict[str, Any] = Field(default_factory=dict)


# ---------- 응답 ----------

class CartLine(BaseModel):
    """카트 한 줄 — enrich 이후 가격 포함."""

    menu: str
    quantity: int
    options: List[str] = Field(default_factory=list)
    base_price: int = 0
    option_total: int = 0
    price: int = 0          # base_price + option_total (1잔)
    line_total: int = 0     # price × quantity


class ToolCallTrace(BaseModel):
    """assistant 1턴에서 호출된 도구 1건의 디버그 메타."""

    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    result: Dict[str, Any] = Field(default_factory=dict)


class CartEventDTO(BaseModel):
    """Cart event log의 한 줄 — 디버그 패널 표시용."""

    action: str
    summary: str
    timestamp: float


class CartResponse(BaseModel):
    session_id: str
    cart: List[CartLine] = Field(default_factory=list)
    total: int = 0


class ChatResponse(CartResponse):
    """채팅 응답 = 카트 + LLM 자연어 reply + 디버그 메타."""

    reply: str
    tool_calls: List[ToolCallTrace] = Field(default_factory=list)
    cart_events: List[CartEventDTO] = Field(default_factory=list)


class CartActionResponse(CartResponse):
    """Visual cart action 응답 — chat에 echo할 짧은 문구 포함."""

    echo: str
    tool_call: ToolCallTrace
    cart_events: List[CartEventDTO] = Field(default_factory=list)


class ClearResponse(BaseModel):
    """세션 초기화 응답."""

    status: str = "ok"
    scope: str  # "single" or "all"
    session_id: Optional[str] = None
    existed: Optional[bool] = None  # scope == "single"일 때만
    removed: Optional[int] = None  # scope == "all"일 때만


class HealthResponse(BaseModel):
    status: str = "healthy"
    service: str = "ediya-cafe-agent"


# ---------- catalog ----------

class CatalogOption(BaseModel):
    kr: str
    en: Optional[str] = None
    price_delta: int = 0


class CatalogOptionCategory(BaseModel):
    kr: str
    en: Optional[str] = None
    applicable_categories: Optional[List[str]] = None  # None = 모든 카테고리에 적용
    is_exclusive: bool = False  # 같은 카테고리 내 상호배타?
    options: List[CatalogOption]


class CatalogMenu(BaseModel):
    kr: str
    en: Optional[str] = None
    category: str
    base_price_l: int = 0
    stock: Optional[int] = None  # None = 무한
    tags: List[str] = Field(default_factory=list)


class CatalogCategory(BaseModel):
    id: str
    descriptor: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)
    menus: List[CatalogMenu]


class CatalogResponse(BaseModel):
    """전체 메뉴/옵션 카탈로그 — UI 카탈로그 패널 + 옵션 picker용."""

    cafe_name: str
    categories: List[CatalogCategory]
    option_categories: List[CatalogOptionCategory]
    slang_aliases: Dict[str, List[str]] = Field(default_factory=dict)
