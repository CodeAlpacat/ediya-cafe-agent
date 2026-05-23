"""Phase 1 RED tests for dispatcher. Cart + menu validation 결합."""
import pytest


@pytest.fixture
def cart_and_dispatch():
    from app.domain.cart import Cart
    from app.dispatcher import dispatch_tool

    cart = Cart()
    return cart, dispatch_tool


def test_dispatch_add_valid_menu(cart_and_dispatch):
    cart, dispatch = cart_and_dispatch
    result = dispatch("add_menu", {"menu": "아이스아메리카노", "quantity": 1, "options": []}, cart)

    assert result["status"] == "ADDED"
    assert result["menu"] == "아이스아메리카노"


def test_dispatch_add_invalid_menu(cart_and_dispatch):
    cart, dispatch = cart_and_dispatch
    result = dispatch("add_menu", {"menu": "냉면", "quantity": 1, "options": []}, cart)

    assert result["status"] == "INVALID_MENU"
    assert "냉면" in result.get("error_detail", "") or result.get("menu") == "냉면"


def test_dispatch_add_invalid_option(cart_and_dispatch):
    cart, dispatch = cart_and_dispatch
    result = dispatch(
        "add_menu",
        {"menu": "아이스아메리카노", "quantity": 1, "options": ["초콜릿시럽"]},
        cart,
    )

    assert result["status"] == "INVALID_OPTION"


def test_dispatch_add_inapplicable_option(cart_and_dispatch):
    """샷추가는 베이커리에 적용 불가."""
    cart, dispatch = cart_and_dispatch
    result = dispatch(
        "add_menu",
        {"menu": "플레인와플", "quantity": 1, "options": ["샷추가"]},
        cart,
    )
    assert result["status"] == "INVALID_OPTION"


def test_dispatch_remove(cart_and_dispatch):
    cart, dispatch = cart_and_dispatch
    dispatch("add_menu", {"menu": "아이스아메리카노", "quantity": 1, "options": []}, cart)

    result = dispatch("remove_menu", {"menu": "아이스아메리카노", "quantity": -1}, cart)
    assert result["status"] == "REMOVED"
    assert cart.is_empty() is True


def test_dispatch_remove_not_in_cart(cart_and_dispatch):
    cart, dispatch = cart_and_dispatch
    result = dispatch("remove_menu", {"menu": "아이스아메리카노", "quantity": 1}, cart)
    assert result["status"] == "MENU_NOT_IN_CART"


def test_dispatch_replace(cart_and_dispatch):
    cart, dispatch = cart_and_dispatch
    dispatch("add_menu", {"menu": "아이스아메리카노", "quantity": 1, "options": []}, cart)

    result = dispatch(
        "replace_menu",
        {"from_menu": "아이스아메리카노", "to_menu": "아이스카페라떼", "to_options": []},
        cart,
    )
    assert result["status"] == "REPLACED"
    assert result["menu"] == "아이스카페라떼"


def test_dispatch_replace_to_invalid_menu(cart_and_dispatch):
    cart, dispatch = cart_and_dispatch
    dispatch("add_menu", {"menu": "아이스아메리카노", "quantity": 1, "options": []}, cart)

    result = dispatch(
        "replace_menu",
        {"from_menu": "아이스아메리카노", "to_menu": "냉면", "to_options": []},
        cart,
    )
    assert result["status"] == "INVALID_MENU"


def test_dispatch_change_option(cart_and_dispatch):
    cart, dispatch = cart_and_dispatch
    dispatch("add_menu", {"menu": "아이스아메리카노", "quantity": 1, "options": []}, cart)

    result = dispatch(
        "change_option",
        {"menu": "아이스아메리카노", "new_options": ["엑스트라"]},
        cart,
    )
    assert result["status"] == "OPTION_CHANGED"
    assert cart.snapshot()[0]["options"] == ["엑스트라"]


def test_dispatch_check_cart_empty(cart_and_dispatch):
    cart, dispatch = cart_and_dispatch
    result = dispatch("check_cart", {}, cart)
    assert result["status"] == "EMPTY_CART"


def test_dispatch_check_cart_with_items(cart_and_dispatch):
    cart, dispatch = cart_and_dispatch
    dispatch("add_menu", {"menu": "아이스아메리카노", "quantity": 1, "options": []}, cart)

    result = dispatch("check_cart", {}, cart)
    assert result["status"] == "CART_VIEW"
    assert len(result["cart"]) == 1


def test_dispatch_inquire_menu_info(cart_and_dispatch):
    cart, dispatch = cart_and_dispatch
    result = dispatch("inquire_menu_info", {"question": "단 거 추천해주세요"}, cart)
    assert result["status"] == "MENU_INQUIRY"


def test_dispatch_done_empty(cart_and_dispatch):
    cart, dispatch = cart_and_dispatch
    result = dispatch("done", {}, cart)
    assert result["status"] == "EMPTY_CART"


def test_dispatch_done_with_items(cart_and_dispatch):
    cart, dispatch = cart_and_dispatch
    dispatch("add_menu", {"menu": "아이스아메리카노", "quantity": 1, "options": []}, cart)

    result = dispatch("done", {}, cart)
    assert result["status"] == "ORDER_COMPLETED"
    assert len(result["cart"]) == 1


def test_dispatch_unknown_tool(cart_and_dispatch):
    cart, dispatch = cart_and_dispatch
    result = dispatch("nuclear_launch", {}, cart)
    assert result["status"] == "UNKNOWN_TOOL"


# ============ Stock 시뮬레이션 ============


def test_dispatch_sold_out(cart_and_dispatch):
    """크림치즈프레첼은 menu_data.yaml에서 stock=0 (품절)."""
    cart, dispatch = cart_and_dispatch
    result = dispatch("add_menu", {"menu": "크림치즈프레첼", "quantity": 1, "options": []}, cart)
    assert result["status"] == "SOLD_OUT"
    assert result["available"] == 0
    assert cart.is_empty()


def test_dispatch_insufficient_stock(cart_and_dispatch):
    """플레인와플은 stock=2. 3개 주문 시 INSUFFICIENT_STOCK."""
    cart, dispatch = cart_and_dispatch
    result = dispatch("add_menu", {"menu": "플레인와플", "quantity": 3, "options": []}, cart)
    assert result["status"] == "INSUFFICIENT_STOCK"
    assert result["available"] == 2
    assert cart.is_empty()


def test_dispatch_stock_partial_then_exceed(cart_and_dispatch):
    """2개 add 성공 후 추가 1개 add 시도 → INSUFFICIENT_STOCK."""
    cart, dispatch = cart_and_dispatch
    r1 = dispatch("add_menu", {"menu": "플레인와플", "quantity": 2, "options": []}, cart)
    assert r1["status"] == "ADDED"

    r2 = dispatch("add_menu", {"menu": "플레인와플", "quantity": 1, "options": []}, cart)
    assert r2["status"] == "INSUFFICIENT_STOCK"
    assert r2["already_in_cart"] == 2
    assert r2["available"] == 0  # 0 = 추가로 가능한 수량


def test_dispatch_unlimited_stock_default(cart_and_dispatch):
    """일반 음료(아메리카노)는 stock 필드 없음 → 무한 재고."""
    cart, dispatch = cart_and_dispatch
    r = dispatch("add_menu", {"menu": "아이스아메리카노", "quantity": 100, "options": []}, cart)
    assert r["status"] == "ADDED"  # 100개도 OK


def test_dispatch_replace_to_sold_out(cart_and_dispatch):
    """카트에 아메리카노 1잔 → 크림치즈프레첼로 replace 시도 → SOLD_OUT."""
    cart, dispatch = cart_and_dispatch
    dispatch("add_menu", {"menu": "아이스아메리카노", "quantity": 1, "options": []}, cart)

    r = dispatch(
        "replace_menu",
        {"from_menu": "아이스아메리카노", "to_menu": "크림치즈프레첼", "to_options": []},
        cart,
    )
    assert r["status"] == "SOLD_OUT"
    # 원본 메뉴는 보존되어야 함
    assert cart.snapshot()[0]["menu"] == "아이스아메리카노"
