"""OpenAI(=Ollama) 클라이언트 팩토리.

기존에는 `app/api/__init__.py` import 사이드이펙트로 모듈 전역 클라이언트가
만들어졌다. 이제 Settings 기반 명시 팩토리 + lru_cache 싱글톤으로 분리한다.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from openai import OpenAI

from app.core.config import Settings, get_settings

logger = logging.getLogger("ediya.llm")


def build_openai_client(settings: Settings | None = None) -> OpenAI:
    """주어진 Settings(없으면 전역)로 OpenAI 호환 클라이언트를 생성."""
    cfg = settings or get_settings()
    client = OpenAI(base_url=cfg.ollama_base_url, api_key=cfg.ollama_api_key)
    logger.info(
        "OpenAI 호환 클라이언트 생성 (base_url=%s, model=%s)",
        cfg.ollama_base_url,
        cfg.ollama_model,
    )
    return client


@lru_cache(maxsize=1)
def get_openai_client() -> OpenAI:
    """프로세스 전역 클라이언트. FastAPI Depends에서 호출."""
    return build_openai_client()
