"""세션 상태 관리.

각 세션은 (Cart, conversation_history) 튜플. 메모리에만 보관.
- timeout 만료 자동 정리
- 동시 요청은 per-session asyncio.Lock으로 직렬화
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.domain.cart import Cart

logger = logging.getLogger("ediya")

MAX_SESSIONS = 50
SESSION_TIMEOUT = 3600  # 1시간


@dataclass
class SessionState:
    cart: Cart = field(default_factory=Cart)
    history: List[Dict[str, Any]] = field(default_factory=list)
    last_activity: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SessionStore:
    """in-memory 세션 저장소. thread-safe하지는 않지만 asyncio 단일 이벤트 루프에서 안전."""

    def __init__(self) -> None:
        self._sessions: Dict[str, SessionState] = {}
        self._global_lock = asyncio.Lock()

    def get_or_create(self, session_id: str) -> SessionState:
        if session_id not in self._sessions:
            if len(self._sessions) >= MAX_SESSIONS:
                # 가장 오래된 세션 제거
                oldest = min(self._sessions.items(), key=lambda kv: kv[1].last_activity)
                logger.info(f"MAX_SESSIONS 초과 — 가장 오래된 세션 제거: {oldest[0]}")
                self._sessions.pop(oldest[0], None)
            self._sessions[session_id] = SessionState()
            logger.info(f"신규 세션 생성: {session_id}")
        state = self._sessions[session_id]
        state.last_activity = time.time()
        return state

    def has(self, session_id: str) -> bool:
        return session_id in self._sessions

    def reset(self, session_id: str) -> bool:
        """해당 세션 제거. 존재했으면 True."""
        existed = self._sessions.pop(session_id, None) is not None
        if existed:
            logger.info(f"세션 초기화: {session_id}")
        return existed

    def reset_all(self) -> int:
        n = len(self._sessions)
        self._sessions.clear()
        logger.info(f"전체 세션 초기화: {n}개 제거")
        return n

    def cleanup_expired(self, timeout: float = SESSION_TIMEOUT) -> int:
        now = time.time()
        expired = [sid for sid, st in self._sessions.items() if now - st.last_activity > timeout]
        for sid in expired:
            self._sessions.pop(sid, None)
        if expired:
            logger.info(f"만료 세션 {len(expired)}개 제거")
        return len(expired)

    def __len__(self) -> int:
        return len(self._sessions)


_store: Optional[SessionStore] = None


def get_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore()
    return _store


async def periodic_cleanup(interval: int = 1800) -> None:
    """30분 주기로 만료 세션 정리."""
    while True:
        try:
            await asyncio.sleep(interval)
            removed = get_store().cleanup_expired()
            if removed:
                logger.info(f"정기 정리: 만료 세션 {removed}개 제거")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"정기 정리 오류: {exc}")
