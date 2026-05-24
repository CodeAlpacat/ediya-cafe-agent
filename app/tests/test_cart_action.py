"""POST /cart_action — visual UI direct dispatch endpoint."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import session_store


@pytest.fixture(autouse=True)
def _reset_store():
    session_store.reset_store_for_tests()
    yield
    session_store.reset_store_for_tests()


@pytest.fixture
def client():
    return TestClient(app)


def test_cart_action_add(client):
    r = client.post("/cart_action", json={
        "session_id": "s1",
        "action": "add_menu",
        "args": {"menu": "아이스아메리카노", "quantity": 1, "options": []},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == "s1"
    assert len(body["cart"]) == 1
    assert body["cart"][0]["menu"] == "아이스아메리카노"
    assert body["echo"].startswith("아이스아메리카노")
    assert body["tool_call"]["name"] == "add_menu"
    assert body["tool_call"]["result"]["status"] == "ADDED"
    assert len(body["cart_events"]) == 1


def test_cart_action_change_option_with_price_delta(client):
    client.post("/cart_action", json={
        "session_id": "s2", "action": "add_menu",
        "args": {"menu": "아이스아메리카노", "quantity": 1, "options": []},
    })
    r = client.post("/cart_action", json={
        "session_id": "s2", "action": "change_option",
        "args": {"menu": "아이스아메리카노", "new_options": ["엑스트라", "투샷추가"]},
    })
    body = r.json()
    line = body["cart"][0]
    # base 4200 (아아) + 엑스트라 1000 + 투샷추가 1000 = 6200
    assert line["base_price"] == 4200
    assert line["option_total"] == 2000
    assert line["price"] == 6200
    assert line["line_total"] == 6200


def test_cart_action_undo(client):
    sid = "s3"
    client.post("/cart_action", json={
        "session_id": sid, "action": "add_menu",
        "args": {"menu": "아이스아메리카노", "quantity": 1, "options": []},
    })
    client.post("/cart_action", json={
        "session_id": sid, "action": "add_menu",
        "args": {"menu": "아이스카페라떼", "quantity": 1, "options": []},
    })
    r = client.post("/cart_action", json={
        "session_id": sid, "action": "undo", "args": {},
    })
    body = r.json()
    assert body["tool_call"]["result"]["status"] == "UNDONE"
    assert len(body["cart"]) == 1
    assert body["cart"][0]["menu"] == "아이스아메리카노"


def test_cart_action_undo_empty_returns_nothing(client):
    r = client.post("/cart_action", json={
        "session_id": "s4", "action": "undo", "args": {},
    })
    body = r.json()
    assert body["tool_call"]["result"]["status"] == "NOTHING_TO_UNDO"
    assert body["echo"] == "되돌릴 변경이 없어요."


def test_cart_action_ambiguous_menu(client):
    r = client.post("/cart_action", json={
        "session_id": "s5", "action": "add_menu",
        "args": {"menu": "라떼", "quantity": 1, "options": []},
    })
    body = r.json()
    assert body["tool_call"]["result"]["status"] == "AMBIGUOUS_MENU"
    assert "candidates" in body["tool_call"]["result"]
    assert len(body["tool_call"]["result"]["candidates"]) > 0


def test_cart_action_shot_options_are_exclusive(client):
    """샷추가 카테고리는 상호배타 — 투샷 선택 시 1샷 자동 제거."""
    sid = "s6"
    client.post("/cart_action", json={
        "session_id": sid, "action": "add_menu",
        "args": {"menu": "아이스아메리카노", "quantity": 1, "options": ["샷추가"]},
    })
    r = client.post("/cart_action", json={
        "session_id": sid, "action": "change_option",
        "args": {"menu": "아이스아메리카노", "new_options": ["투샷추가"]},
    })
    line = r.json()["cart"][0]
    assert "샷추가" not in line["options"]
    assert "투샷추가" in line["options"]


def test_catalog_endpoint(client):
    r = client.get("/menu/catalog")
    assert r.status_code == 200
    body = r.json()
    assert body["cafe_name"]
    # 카테고리에 메뉴가 있어야
    assert len(body["categories"]) > 0
    coffee = next((c for c in body["categories"] if c["id"] == "coffee"), None)
    assert coffee is not None
    assert any(m["kr"] == "아이스아메리카노" for m in coffee["menus"])
    # 옵션 카테고리에 샷추가의 새 옵션들 포함
    shot = next((oc for oc in body["option_categories"] if oc["kr"] == "샷추가"), None)
    assert shot is not None
    opt_names = [o["kr"] for o in shot["options"]]
    assert "투샷추가" in opt_names
    assert "트리플샷추가" in opt_names
    assert shot["is_exclusive"] is True  # 우리가 _EXCLUSIVE에 추가했음
