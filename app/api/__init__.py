"""API 모듈 초기화.

Ollama (OpenAI 호환) 클라이언트와 로거를 미리 띄워두고,
요청/응답 모델을 한 곳에서 노출한다.
"""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


def setup_logging() -> logging.Logger:
    log = logging.getLogger("ediya")
    log.setLevel(logging.DEBUG)
    for h in log.handlers[:]:
        log.removeHandler(h)
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(name)s - %(levelname)s - %(message)s")
    )
    log.addHandler(handler)
    return log


logger = setup_logging()


OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:e2b")

try:
    openai_client = OpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")
    logger.info(f"Ollama 클라이언트 초기화 완료 (base_url={OLLAMA_BASE_URL}, model={OLLAMA_MODEL})")
except Exception as exc:
    logger.error(f"Ollama 클라이언트 초기화 실패: {exc}")
    openai_client = None


MODEL_INFO = {"default": OLLAMA_MODEL}


from .models import (
    ChatResponse,
    HealthResponse,
    MessageRequest,
    SessionRequest,
)

__all__ = [
    "openai_client",
    "logger",
    "OLLAMA_BASE_URL",
    "OLLAMA_MODEL",
    "MODEL_INFO",
    "MessageRequest",
    "SessionRequest",
    "ChatResponse",
    "HealthResponse",
]
