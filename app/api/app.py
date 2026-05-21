"""Ediya Cafe Agent API 서버.

FastAPI + Ollama gemma4:e2b 백엔드. 단일 사용자 데모를 가정한 in-memory 세션.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.menu import get_menu_price

from . import (
    MessageRequest,
    SessionRequest,
    logger as api_logger,
    MODEL_INFO,
)
from .conversation_handler import process_message
from .middleware import log_requests
from .session_manager import get_store, periodic_cleanup


def _enrich_cart(items: list) -> list:
    """cart 항목에 단가/소계 추가. 메뉴 lookup 실패 시 0."""
    out = []
    total = 0
    for it in items:
        price = get_menu_price(it["menu"]) or 0
        line_total = price * it["quantity"]
        total += line_total
        out.append({**it, "price": price, "line_total": line_total})
    return out

logger = api_logger

app = FastAPI(
    title="Ediya Cafe Agent",
    description="이디야 커피 주문 에이전트 데모 API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.middleware("http")(log_requests)


_cleanup_task: asyncio.Task | None = None


@app.on_event("startup")
async def startup_event() -> None:
    logger.info("Ediya Cafe Agent API 서버 시작")
    logger.info(f"모델: {MODEL_INFO.get('default')}")
    global _cleanup_task
    _cleanup_task = asyncio.create_task(periodic_cleanup())
    logger.info("세션 정리 태스크 시작")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    if _cleanup_task is not None:
        _cleanup_task.cancel()
    logger.info("Ediya Cafe Agent API 서버 종료")


@app.post("/chat")
async def chat(request: MessageRequest) -> dict:
    """사용자 메시지 1턴 처리. session_id 없으면 새로 발급."""
    session_id = request.session_id or f"session_{uuid.uuid4().hex[:12]}"
    store = get_store()
    state = store.get_or_create(session_id)

    logger.info(f"[CHAT] session={session_id} message={request.message[:80]}")

    async with state.lock:
        try:
            reply, cart_snapshot = await process_message(
                message=request.message,
                cart=state.cart,
                history=state.history,
            )
        except Exception as exc:
            logger.error(f"chat 처리 오류: {exc}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc))

    enriched = _enrich_cart(cart_snapshot)
    return {
        "reply": reply,
        "session_id": session_id,
        "cart": enriched,
        "total": sum(it["line_total"] for it in enriched),
    }


@app.get("/cart")
async def get_cart(session_id: str) -> dict:
    """세션별 카트 조회."""
    store = get_store()
    if not store.has(session_id):
        return {"session_id": session_id, "cart": [], "total": 0}
    state = store.get_or_create(session_id)
    enriched = _enrich_cart(state.cart.snapshot())
    return {
        "session_id": session_id,
        "cart": enriched,
        "total": sum(it["line_total"] for it in enriched),
    }


@app.post("/clear")
async def clear(request: SessionRequest | None = None) -> dict:
    """세션 초기화. session_id 있으면 해당 세션만, 없으면 전체."""
    store = get_store()
    if request and request.session_id:
        existed = store.reset(request.session_id)
        return {
            "status": "ok",
            "scope": "single",
            "session_id": request.session_id,
            "existed": existed,
        }
    removed = store.reset_all()
    return {"status": "ok", "scope": "all", "removed": removed}


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "service": "ediya-cafe-agent"}


# 정적 데모 페이지 mount
_STATIC_DIR = Path(__file__).resolve().parent / "static"
_INDEX_HTML = _STATIC_DIR / "index.html"

if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/")
async def root():
    if _INDEX_HTML.exists():
        return FileResponse(str(_INDEX_HTML))
    return {
        "service": "ediya-cafe-agent",
        "docs": "/docs",
        "note": "static demo page not bundled",
    }
