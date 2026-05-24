"""FastAPI Depends 프로바이더. 라우터는 여기서만 의존성을 꺼낸다.

기존엔 `get_store()` 수동 싱글톤을 라우터가 직접 호출하고 OpenAI 클라이언트는
모듈 import 시점에 만들어졌다. 이제 Depends로 일원화 — 테스트에서
`app.dependency_overrides`로 갈아끼울 수 있다.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from openai import OpenAI

from app.core.config import Settings, get_settings
from app.llm.client import get_openai_client
from app.services.session_store import SessionStore, get_store


def settings_dep() -> Settings:
    return get_settings()


def session_store_dep() -> SessionStore:
    return get_store()


def openai_client_dep() -> OpenAI:
    return get_openai_client()


# 라우터 시그니처를 짧게 쓰기 위한 별칭.
SettingsDep = Annotated[Settings, Depends(settings_dep)]
SessionStoreDep = Annotated[SessionStore, Depends(session_store_dep)]
OpenAIClientDep = Annotated[OpenAI, Depends(openai_client_dep)]
