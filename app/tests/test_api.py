"""FastAPI 엔드포인트 테스트.

LLM 호출은 mock으로 격리. 세션/카트/응답 형태만 검증.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.app import app
from app.api import session_manager


@pytest.fixture(autouse=True)
def _reset_store():
    """매 테스트마다 SessionStore 초기화."""
    session_manager._store = None
    yield
    session_manager._store = None


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_process_message(monkeypatch):
    """conversation_handler.process_message를 결정론적 mock으로 교체.

    "아메리카노" 포함하면 add_menu 흉내 — cart에 직접 항목 추가.
    """
    async def _mock(message, cart, history, config=None):
        if "아메리카노" in message:
            cart.add(menu="아이스아메리카노", quantity=1, options=[])
            reply = "아이스아메리카노 한 잔 담아드렸어요."
        elif "초기화" in message or "비워" in message:
            reply = "카트 초기화는 위쪽 버튼을 눌러주세요."
        else:
            reply = "주문하실 메뉴를 말씀해 주세요."
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": reply})
        return reply, cart.snapshot()

    monkeypatch.setattr(
        "app.api.app.process_message", _mock, raising=True
    )


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy"
    assert body["service"] == "ediya-cafe-agent"


def test_chat_creates_session_when_missing(client, mock_process_message):
    r = client.post("/chat", json={"message": "아이스 아메리카노 한 잔 주세요"})
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"].startswith("session_")
    assert "담아드렸" in body["reply"]
    assert len(body["cart"]) == 1
    assert body["cart"][0]["menu"] == "아이스아메리카노"


def test_chat_reuses_session_history(client, mock_process_message):
    r1 = client.post("/chat", json={"session_id": "s1", "message": "아이스 아메리카노 주세요"})
    assert r1.status_code == 200
    assert len(r1.json()["cart"]) == 1

    r2 = client.post("/chat", json={"session_id": "s1", "message": "아메리카노 하나 더"})
    assert r2.status_code == 200
    # 같은 메뉴 다시 추가 → 수량 누적되거나 항목 추가됨
    cart = r2.json()["cart"]
    total_qty = sum(it["quantity"] for it in cart)
    assert total_qty == 2


def test_get_cart_empty_for_unknown_session(client):
    r = client.get("/cart", params={"session_id": "never-seen"})
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == "never-seen"
    assert body["cart"] == []
    assert body["total"] == 0


def test_get_cart_returns_state(client, mock_process_message):
    client.post("/chat", json={"session_id": "s2", "message": "아메리카노"})
    r = client.get("/cart", params={"session_id": "s2"})
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == "s2"
    assert len(body["cart"]) == 1


def test_clear_single_session(client, mock_process_message):
    client.post("/chat", json={"session_id": "s3", "message": "아메리카노"})
    r = client.post("/clear", json={"session_id": "s3"})
    assert r.status_code == 200
    body = r.json()
    assert body["scope"] == "single"
    assert body["existed"] is True

    # 그 다음 chat은 fresh history
    r2 = client.get("/cart", params={"session_id": "s3"})
    assert r2.json()["cart"] == []


def test_clear_all_sessions(client, mock_process_message):
    client.post("/chat", json={"session_id": "s4", "message": "아메리카노"})
    client.post("/chat", json={"session_id": "s5", "message": "아메리카노"})
    r = client.post("/clear")
    assert r.status_code == 200
    body = r.json()
    assert body["scope"] == "all"
    assert body["removed"] == 2


def test_clear_nonexistent_session_returns_existed_false(client):
    r = client.post("/clear", json={"session_id": "ghost"})
    assert r.status_code == 200
    assert r.json()["existed"] is False


def test_root_serves_index_html_if_present(client, tmp_path):
    # static/index.html이 있으면 200, 없으면 dict — 둘 다 OK
    r = client.get("/")
    assert r.status_code == 200
