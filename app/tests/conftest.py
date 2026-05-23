"""Fixtures for cafe agent tests. Unit tests + Ollama integration."""
import os
import sys
from pathlib import Path

import pytest

# 프로젝트 루트를 sys.path에 추가 (pytest를 루트에서 실행할 때 app 모듈 import 가능)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def fresh_cart():
    from app.domain.cart import Cart
    return Cart()


@pytest.fixture(scope="session")
def loaded_menu():
    from app.domain.menu import load_menu
    return load_menu()


def _ollama_available() -> bool:
    """Ollama 데몬이 떠 있고 gemma4:e2b가 설치되어 있는지 확인."""
    try:
        from openai import OpenAI

        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        client = OpenAI(base_url=base_url, api_key="ollama")
        models = client.models.list()
        return any("gemma4:e2b" in m.id for m in models.data)
    except Exception:
        return False


@pytest.fixture(scope="session")
def ollama_client():
    """Live OpenAI 호환 Ollama 클라이언트. 미가용 시 모든 ollama 마크 테스트 skip."""
    if not _ollama_available():
        pytest.skip("Ollama 데몬 미실행 또는 gemma4:e2b 미설치")
    from openai import OpenAI

    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    return OpenAI(base_url=base_url, api_key="ollama")


@pytest.fixture(scope="session")
def model_name() -> str:
    return os.getenv("OLLAMA_MODEL", "gemma4:e2b")
