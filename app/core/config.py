"""앱 전역 설정.

기존에는 환경변수가 세 곳(`app/api/__init__.py`, `app/cafe_profile.py`,
`app/llm/agent.py`)에서 따로 읽혔다. 하나의 `Settings`로 모아 import 순서와
무관하게 어디서나 같은 값을 본다. pydantic-settings로 검증 + .env 로드까지 일임.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """프로세스 전역 설정. 값은 환경변수 또는 `.env`에서 로드."""

    # Ollama (OpenAI 호환) 엔드포인트
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "gemma4:e2b"
    ollama_api_key: str = "ollama"  # Ollama는 실제 검증 안 함, 자리채움

    # 카페 프로필 — app/cafes/<이름>/ 폴더와 1:1
    cafe_profile: str = "ediya"

    # 세션 한도. 데모 기본값.
    max_sessions: int = 50
    session_timeout_seconds: int = 3600
    session_cleanup_interval_seconds: int = 1800

    # Agent 동작
    agent_temperature: float = 0.0
    agent_seed: int = 4242
    agent_max_round_trips: int = 5

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """프로세스 수명 동안 단일 Settings 인스턴스. 테스트는 cache_clear()로 reset."""
    return Settings()
