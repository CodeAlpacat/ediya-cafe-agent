"""카트 조회 + 세션 초기화 + visual UI 직접 액션."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.api.deps import SessionStoreDep
from app.api.schemas import (
    CartActionRequest,
    CartActionResponse,
    CartEventDTO,
    CartLine,
    CartResponse,
    ClearResponse,
    SessionRequest,
    ToolCallTrace,
)
from app.dispatcher import dispatch_tool
from app.services.cart_pricing import enrich_with_total

logger = logging.getLogger("ediya")

router = APIRouter(tags=["cart"])


@router.get("/cart", response_model=CartResponse)
async def get_cart(session_id: str, store: SessionStoreDep) -> CartResponse:
    """세션별 카트 조회. 세션이 없으면 빈 카트로 응답."""
    if not store.has(session_id):
        return CartResponse(session_id=session_id, cart=[], total=0)
    state = store.get_or_create(session_id)
    cart, total = enrich_with_total(state.cart.snapshot())
    return CartResponse(
        session_id=session_id,
        cart=[CartLine(**c) for c in cart],
        total=total,
    )


@router.post("/clear", response_model=ClearResponse)
async def clear(
    store: SessionStoreDep,
    request: SessionRequest | None = None,
) -> ClearResponse:
    """세션 초기화. session_id 있으면 해당 세션만, 없으면 전체."""
    if request and request.session_id:
        existed = store.reset(request.session_id)
        return ClearResponse(
            scope="single", session_id=request.session_id, existed=existed
        )
    removed = store.reset_all()
    return ClearResponse(scope="all", removed=removed)


# ---------- visual UI 직접 액션 ----------

# dispatcher status → user-facing 한 줄 echo
_ECHO_BY_STATUS = {
    "ADDED":              lambda r: f"{r.get('menu')} {r.get('quantity', 1)}잔 담았어요.",
    "INCREMENTED":        lambda r: f"{r.get('menu')} 추가했어요 (총 {r.get('quantity')}잔).",
    "REMOVED":            lambda r: (
        "카트 전체를 비웠어요." if r.get("menu") == "ALL"
        else (f"{r.get('menu')} 전체를 뺐어요." if r.get("quantity") == 0
              else f"{r.get('menu')} {r.get('quantity')}잔만 남겼어요.")
    ),
    "REPLACED":           lambda r: f"{r.get('from_menu')} → {r.get('menu')}으로 바꿨어요.",
    "OPTION_CHANGED":     lambda r: (
        f"{r.get('menu')} 옵션을 {', '.join(r.get('options', [])) or '비움'}(으)로 변경했어요."
    ),
    "UNDONE":             lambda r: f"방금 한 변경을 되돌렸어요. ({r.get('restored_summary')})",
    "NOTHING_TO_UNDO":    lambda r: "되돌릴 변경이 없어요.",
    "MENU_NOT_IN_CART":   lambda r: f"{r.get('menu')}는 카트에 없어요.",
    "INVALID_MENU":       lambda r: r.get("error_detail") or "그 메뉴는 매장에 없어요.",
    "AMBIGUOUS_MENU":     lambda r: r.get("error_detail") or "메뉴를 더 구체적으로 알려주세요.",
    "INVALID_OPTION":     lambda r: r.get("error_detail") or "그 옵션은 적용할 수 없어요.",
    "SOLD_OUT":           lambda r: r.get("error_detail") or "품절된 메뉴예요.",
    "INSUFFICIENT_STOCK": lambda r: r.get("error_detail") or "재고가 부족해요.",
}


def _build_echo(result: dict) -> str:
    status = result.get("status", "UNKNOWN")
    builder = _ECHO_BY_STATUS.get(status)
    if builder is None:
        return f"처리됨 ({status})"
    try:
        return builder(result)
    except Exception:
        return f"처리됨 ({status})"


@router.post("/cart_action", response_model=CartActionResponse)
async def cart_action(
    request: CartActionRequest,
    store: SessionStoreDep,
) -> CartActionResponse:
    """카탈로그·옵션 picker 등 visual UI에서 dispatcher를 직접 호출.

    LLM round-trip을 건너뛰어 응답성 < 200ms 보장. 검증은 dispatcher 통일.
    """
    if not store.has(request.session_id):
        # 세션이 없어도 만들어서 처리 (chat 첫 요청 전에 visual 액션 가능)
        pass
    state = store.get_or_create(request.session_id)

    async with state.lock:
        events_before = len(state.cart.history_snapshot())
        try:
            result = dispatch_tool(request.action, request.args, state.cart)
        except Exception as exc:  # pragma: no cover
            logger.error("cart_action 처리 오류: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc))
        new_events = state.cart.history_snapshot()[events_before:]

    cart, total = enrich_with_total(state.cart.snapshot())
    return CartActionResponse(
        session_id=request.session_id,
        cart=[CartLine(**c) for c in cart],
        total=total,
        echo=_build_echo(result),
        tool_call=ToolCallTrace(
            name=request.action, arguments=request.args, result=result
        ),
        cart_events=[
            CartEventDTO(action=e["action"], summary=e["summary"], timestamp=e["timestamp"])
            for e in new_events
        ],
    )
