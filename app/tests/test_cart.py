"""Phase 1 RED tests for Cart. 구현은 아직 없음 — 모두 실패해야 정상."""
import pytest


def test_new_cart_is_empty(fresh_cart):
    assert fresh_cart.is_empty() is True
    assert fresh_cart.snapshot() == []


def test_add_new_menu_creates_item(fresh_cart):
    result = fresh_cart.add(menu="아이스아메리카노", quantity=1, options=[])

    assert result["status"] == "ADDED"
    assert result["menu"] == "아이스아메리카노"
    assert result["quantity"] == 1
    assert fresh_cart.snapshot() == [
        {"menu": "아이스아메리카노", "quantity": 1, "options": []}
    ]


def test_add_same_menu_same_options_increments(fresh_cart):
    fresh_cart.add(menu="아이스아메리카노", quantity=1, options=[])
    result = fresh_cart.add(menu="아이스아메리카노", quantity=2, options=[])

    assert result["status"] == "INCREMENTED"
    assert result["quantity"] == 3
    assert fresh_cart.snapshot() == [
        {"menu": "아이스아메리카노", "quantity": 3, "options": []}
    ]


def test_add_same_menu_different_options_creates_separate_item(fresh_cart):
    fresh_cart.add(menu="아이스아메리카노", quantity=1, options=[])
    fresh_cart.add(menu="아이스아메리카노", quantity=1, options=["엑스트라"])

    snapshot = fresh_cart.snapshot()
    assert len(snapshot) == 2
    assert {"menu": "아이스아메리카노", "quantity": 1, "options": []} in snapshot
    assert {"menu": "아이스아메리카노", "quantity": 1, "options": ["엑스트라"]} in snapshot


def test_add_options_order_independent(fresh_cart):
    """옵션 순서 달라도 같은 항목으로 취급."""
    fresh_cart.add(menu="아이스카페라떼", quantity=1, options=["엑스트라", "샷추가"])
    result = fresh_cart.add(menu="아이스카페라떼", quantity=1, options=["샷추가", "엑스트라"])

    assert result["status"] == "INCREMENTED"
    assert result["quantity"] == 2
    assert len(fresh_cart.snapshot()) == 1


def test_remove_full_quantity_deletes_item(fresh_cart):
    fresh_cart.add(menu="아이스아메리카노", quantity=2, options=[])
    result = fresh_cart.remove(menu="아이스아메리카노", quantity=-1)

    assert result["status"] == "REMOVED"
    assert fresh_cart.is_empty() is True


def test_remove_partial_quantity_decrements(fresh_cart):
    fresh_cart.add(menu="아이스아메리카노", quantity=3, options=[])
    result = fresh_cart.remove(menu="아이스아메리카노", quantity=1)

    assert result["status"] == "REMOVED"
    assert result["quantity"] == 2  # 남은 수량
    assert fresh_cart.snapshot()[0]["quantity"] == 2


def test_remove_more_than_existing_removes_item(fresh_cart):
    fresh_cart.add(menu="아이스아메리카노", quantity=1, options=[])
    result = fresh_cart.remove(menu="아이스아메리카노", quantity=5)

    assert result["status"] == "REMOVED"
    assert fresh_cart.is_empty() is True


def test_remove_not_found(fresh_cart):
    result = fresh_cart.remove(menu="아이스아메리카노", quantity=1)
    assert result["status"] == "MENU_NOT_IN_CART"


def test_remove_all_clears_cart(fresh_cart):
    fresh_cart.add(menu="아이스아메리카노", quantity=1, options=[])
    fresh_cart.add(menu="핫카페라떼", quantity=2, options=[])

    result = fresh_cart.remove(menu="ALL", quantity=-1)
    assert result["status"] == "REMOVED"
    assert fresh_cart.is_empty() is True


def test_replace_swaps_menu(fresh_cart):
    fresh_cart.add(menu="아이스아메리카노", quantity=1, options=[])
    result = fresh_cart.replace(
        from_menu="아이스아메리카노",
        to_menu="아이스카페라떼",
        to_options=[],
    )

    assert result["status"] == "REPLACED"
    assert result["menu"] == "아이스카페라떼"
    assert fresh_cart.snapshot() == [
        {"menu": "아이스카페라떼", "quantity": 1, "options": []}
    ]


def test_replace_preserves_quantity(fresh_cart):
    fresh_cart.add(menu="아이스아메리카노", quantity=3, options=[])
    fresh_cart.replace(
        from_menu="아이스아메리카노",
        to_menu="아이스카페모카",
        to_options=["엑스트라"],
    )

    assert fresh_cart.snapshot() == [
        {"menu": "아이스카페모카", "quantity": 3, "options": ["엑스트라"]}
    ]


def test_replace_menu_not_found(fresh_cart):
    result = fresh_cart.replace(
        from_menu="아이스아메리카노", to_menu="아이스카페라떼", to_options=[]
    )
    assert result["status"] == "MENU_NOT_IN_CART"


def test_change_option_replaces_options(fresh_cart):
    fresh_cart.add(menu="아이스아메리카노", quantity=1, options=[])
    result = fresh_cart.change_option(
        menu="아이스아메리카노",
        new_options=["엑스트라", "샷추가"],
    )

    assert result["status"] == "OPTION_CHANGED"
    assert fresh_cart.snapshot()[0]["options"] == ["엑스트라", "샷추가"]


def test_change_option_menu_not_found(fresh_cart):
    result = fresh_cart.change_option(menu="아이스아메리카노", new_options=["엑스트라"])
    assert result["status"] == "MENU_NOT_IN_CART"


def test_snapshot_returns_immutable_copy(fresh_cart):
    fresh_cart.add(menu="아이스아메리카노", quantity=1, options=["엑스트라"])
    snap = fresh_cart.snapshot()
    snap[0]["quantity"] = 999
    snap[0]["options"].append("hack")

    # 외부 변경이 카트 내부 상태에 영향 없어야 함
    real = fresh_cart.snapshot()
    assert real[0]["quantity"] == 1
    assert real[0]["options"] == ["엑스트라"]


def test_clear_empties_cart(fresh_cart):
    fresh_cart.add(menu="아이스아메리카노", quantity=1, options=[])
    fresh_cart.add(menu="핫카페라떼", quantity=1, options=[])
    fresh_cart.clear()
    assert fresh_cart.is_empty() is True
