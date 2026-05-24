"""세션 상태 관리.

각 세션은 (Cart, conversation_history) 묶음. in-memory에만 보관.
- timeout 만료 자동 정리
- 동시 요청은 per-session asyncio.Lock으로 직렬화
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core.config import get_settings
from app.domain.cart import Cart

logger = logging.getLogger("ediya.session")

# 호환을 위해 모듈 상수로도 노출 (기존 코드는 직접 import해서 썼다).
MAX_SESSIONS = get_settings().max_sessions
SESSION_TIMEOUT = get_settings().session_timeout_seconds


@dataclass
class SessionState:
    cart: Cart = field(default_factory=Cart)
    history: List[Dict[str, Any]] = field(default_factory=list)
    last_activity: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SessionStore:
    """in-memory 세션 저장소. asyncio 단일 이벤트 루프 가정."""

    def __init__(
        self,
        max_sessions: int | None = None,
        session_timeout: int | None = None,
    ) -> None:
        self._sessions: Dict[str, SessionState] = {}
        self._max_sessions = max_sessions if max_sessions is not None else MAX_SESSIONS
        self._timeout = session_timeout if session_timeout is not None else SESSION_TIMEOUT

    def get_or_create(self, session_id: str) -> SessionState:
        if session_id not in self._sessions:
            if len(self._sessions) >= self._max_sessions:
                oldest_id, _ = min(
                    self._sessions.items(), key=lambda kv: kv[1].last_activity
                )
                logger.info("MAX_SESSIONS 초과 — 가장 오래된 세션 제거: %s", oldest_id)
                self._sessions.pop(oldest_id, None)
            self._sessions[session_id] = SessionState()
            logger.info("신규 세션 생성: %s", session_id)
        state = self._sessions[session_id]
        state.last_activity = time.time()
        return state

    def has(self, session_id: str) -> bool:
        return session_id in self._sessions

    def reset(self, session_id: str) -> bool:
        """해당 세션 제거. 존재했으면 True."""
        existed = self._sessions.pop(session_id, None) is not None
        if existed:
            logger.info("세션 초기화: %s", session_id)
        return existed

    def reset_all(self) -> int:
        n = len(self._sessions)
        self._sessions.clear()
        logger.info("전체 세션 초기화: %d개 제거", n)
        return n

    def cleanup_expired(self, timeout: float | None = None) -> int:
        t = timeout if timeout is not None else self._timeout
        now = time.time()
        expired = [
            sid for sid, st in self._sessions.items() if now - st.last_activity > t
        ]
        for sid in expired:
            self._sessions.pop(sid, None)
        if expired:
            logger.info("만료 세션 %d개 제거", len(expired))
        return len(expired)

    def __len__(self) -> int:
        return len(self._sessions)


# 프로세스 단일 인스턴스 — FastAPI Depends에서 노출.
_store: Optional[SessionStore] = None


def get_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore()
    return _store


def reset_store_for_tests() -> None:
    """테스트에서 격리용. 운영 코드에서는 호출하지 말 것."""
    global _store
    _store = None


async def periodic_cleanup(interval: int | None = None) -> None:
    """주기적으로 만료 세션 정리. lifespan startup에서 task로 실행."""
    if interval is None:
        interval = get_settings().session_cleanup_interval_seconds
    while True:
        try:
            await asyncio.sleep(interval)
            removed = get_store().cleanup_expired()
            if removed:
                logger.info("정기 정리: 만료 세션 %d개 제거", removed)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover — 방어
            logger.error("정기 정리 오류: %s", exc)
