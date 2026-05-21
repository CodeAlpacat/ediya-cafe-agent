"""FastAPI 미들웨어."""
from __future__ import annotations

import json
import logging
import time

from fastapi import Request

logger = logging.getLogger("ediya")


async def log_requests(request: Request, call_next):
    """모든 HTTP 요청과 응답을 로깅."""
    start = time.time()

    body_text: str | None = None
    if request.method == "POST":
        try:
            body = await request.body()
            body_text = body.decode("utf-8") if body else None
            # Request 재구성 — body는 1회용
            request = Request(
                request.scope,
                receive=lambda: {"type": "http.request", "body": body},
            )
        except Exception:
            body_text = "<unreadable body>"

    session_id = None
    if body_text:
        try:
            data = json.loads(body_text)
            session_id = data.get("session_id")
        except Exception:
            pass

    if session_id:
        sess_logger = logging.getLogger(f"ediya.session.{session_id}")
        sess_logger.info(f"[REQUEST] {request.method} {request.url.path}")
        if body_text:
            sess_logger.debug(f"[REQUEST BODY] {body_text}")

    response = await call_next(request)
    elapsed = time.time() - start

    if session_id:
        sess_logger.info(
            f"[RESPONSE] status={response.status_code} elapsed={elapsed:.3f}s"
        )

    response.headers["X-Process-Time"] = f"{elapsed:.3f}"
    return response
