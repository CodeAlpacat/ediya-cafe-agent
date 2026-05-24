"""FastAPI 앱 팩토리 + lifespan.

기존 `app/api/app.py`의 모듈-전역 app, 사이드이펙트 로깅, deprecated
`@app.on_event`를 정리한다. 엔트리포인트는 `app.main:app`.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.middleware import log_requests
from app.api.routers import cart, catalog, chat, health, static
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.services.session_store import periodic_cleanup


@asynccontextmanager
async def lifespan(app: FastAPI):
    """startup: 로깅 셋업 + 세션 정리 태스크 기동. shutdown: 태스크 취소."""
    log = setup_logging()
    settings = get_settings()
    log.info("Ediya Cafe Agent API 서버 시작 (model=%s)", settings.ollama_model)

    cleanup_task = asyncio.create_task(periodic_cleanup())
    log.info("세션 정리 태스크 시작")
    try:
        yield
    finally:
        cleanup_task.cancel()
        log.info("Ediya Cafe Agent API 서버 종료")


def create_app() -> FastAPI:
    """애플리케이션 팩토리. 테스트/운영에서 동일하게 호출."""
    app = FastAPI(
        title="Ediya Cafe Agent",
        description="이디야 커피 주문 에이전트 데모 API",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.middleware("http")(log_requests)

    app.include_router(chat.router)
    app.include_router(cart.router)
    app.include_router(catalog.router)
    app.include_router(health.router)
    app.include_router(static.router)
    static.mount_static(app)

    return app


# uvicorn 엔트리포인트 — `uvicorn app.main:app`.
app = create_app()
