"""Cart event log + undo 단위 테스트."""
from __future__ import annotations


def test_history_starts_empty(fresh_cart):
    assert fresh_cart.history_snapshot() == []


def test_add_records_event(fresh_cart):
    fresh_cart.add(menu="아이스아메리카노", quantity=2, options=[])
    h = fresh_cart.history_snapshot()
    assert len(h) == 1
    assert h[0]["action"] == "add"
    assert h[0]["before"] == []
    assert h[0]["after"] == [
        {"menu": "아이스아메리카노", "quantity": 2, "options": []}
    ]


def test_undo_restores_previous_state(fresh_cart):
    fresh_cart.add(menu="아이스아메리카노", quantity=1, options=[])
    fresh_cart.add(menu="아이스카페라떼", quantity=1, options=[])
    assert len(fresh_cart.snapshot()) == 2

    r = fresh_cart.undo()
    assert r["status"] == "UNDONE"
    assert r["restored_action"] == "add"
    assert len(fresh_cart.snapshot()) == 1
    assert fresh_cart.snapshot()[0]["menu"] == "아이스아메리카노"


def test_undo_empty_returns_nothing_to_undo(fresh_cart):
    r = fresh_cart.undo()
    assert r["status"] == "NOTHING_TO_UNDO"


def test_undo_after_remove_restores_item(fresh_cart):
    fresh_cart.add(menu="아이스아메리카노", quantity=1, options=[])
    fresh_cart.remove(menu="아이스아메리카노", quantity=-1)
    assert fresh_cart.is_empty()

    r = fresh_cart.undo()
    assert r["status"] == "UNDONE"
    assert fresh_cart.snapshot() == [
        {"menu": "아이스아메리카노", "quantity": 1, "options": []}
    ]


def test_undo_change_option(fresh_cart):
    fresh_cart.add(menu="아이스아메리카노", quantity=1, options=[])
    fresh_cart.change_option(menu="아이스아메리카노", new_options=["엑스트라"])
    assert fresh_cart.snapshot()[0]["options"] == ["엑스트라"]

    fresh_cart.undo()
    assert fresh_cart.snapshot()[0]["options"] == []


def test_history_chain_can_undo_multiple_steps(fresh_cart):
    fresh_cart.add(menu="아이스아메리카노", quantity=1, options=[])
    fresh_cart.add(menu="아이스카페라떼", quantity=1, options=[])
    fresh_cart.change_option(menu="아이스아메리카노", new_options=["엑스트라"])
    assert len(fresh_cart.history_snapshot()) == 3

    fresh_cart.undo()  # change_option revert
    fresh_cart.undo()  # 라떼 add revert
    snap = fresh_cart.snapshot()
    assert len(snap) == 1
    assert snap[0]["menu"] == "아이스아메리카노"
    assert snap[0]["options"] == []


def test_undo_pops_history_so_next_undo_targets_earlier(fresh_cart):
    fresh_cart.add(menu="아이스아메리카노", quantity=1, options=[])
    fresh_cart.add(menu="아이스카페라떼", quantity=1, options=[])
    fresh_cart.undo()
    fresh_cart.undo()
    assert fresh_cart.is_empty()
    assert fresh_cart.undo()["status"] == "NOTHING_TO_UNDO"
