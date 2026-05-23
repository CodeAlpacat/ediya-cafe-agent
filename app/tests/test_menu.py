"""Phase 1 RED tests for menu loader / validators."""
import pytest


def test_load_menu_returns_dict_with_expected_keys(loaded_menu):
    assert "menus" in loaded_menu
    assert "option_categories" in loaded_menu
    assert isinstance(loaded_menu["menus"], list)
    assert isinstance(loaded_menu["option_categories"], list)


def test_menu_count_is_around_52(loaded_menu):
    """Ediya 메뉴 52종 (시즌 빙수 포함). ±10 허용."""
    assert 40 <= len(loaded_menu["menus"]) <= 70


def test_menu_has_required_fields(loaded_menu):
    for item in loaded_menu["menus"]:
        assert "kr" in item, f"missing kr in {item}"
        assert "en" in item, f"missing en in {item}"
        assert "category" in item, f"missing category in {item}"


def test_signature_menus_present(loaded_menu):
    krs = {m["kr"] for m in loaded_menu["menus"]}
    for name in [
        "아이스아메리카노", "핫아메리카노", "아이스카페라떼", "핫카페라떼",
        "콜드브루아메리카노", "디카페인콜드브루아메리카노",
        "모카플랫치노", "흑당버블티",
    ]:
        assert name in krs, f"signature menu missing: {name}"


def test_size_options_are_large_and_extra(loaded_menu):
    size_cat = next(c for c in loaded_menu["option_categories"] if c["kr"] == "사이즈")
    size_kr = [o["kr"] for o in size_cat["options"]]
    assert "라지" in size_kr
    assert "엑스트라" in size_kr
    # 벤티 시스템은 드롭됨
    assert "벤티" not in size_kr
    assert "더벤티" not in size_kr


def test_size_is_not_essential(loaded_menu):
    """Ediya는 사이즈 미명시 시 라지 기본. essential=False."""
    size_cat = next(c for c in loaded_menu["option_categories"] if c["kr"] == "사이즈")
    assert size_cat["is_essential"] is False


def test_no_bean_category(loaded_menu):
    """Ediya는 원두 옵션 없음."""
    cat_names = [c["kr"] for c in loaded_menu["option_categories"]]
    assert "원두" not in cat_names


def test_decaf_is_a_menu_category_not_option(loaded_menu):
    """디카페인은 별도 메뉴로 분리됨 (옵션이 아님)."""
    decaf_menus = [m for m in loaded_menu["menus"] if m["category"] == "decaf"]
    assert len(decaf_menus) >= 4

    decaf_krs = {m["kr"] for m in decaf_menus}
    assert "디카페인콜드브루아메리카노" in decaf_krs


def test_is_valid_menu(loaded_menu):
    from app.domain.menu import is_valid_menu

    assert is_valid_menu("아이스아메리카노") is True
    assert is_valid_menu("디카페인콜드브루아메리카노") is True
    assert is_valid_menu("냉면") is False
    assert is_valid_menu("") is False


def test_is_valid_option_in_category(loaded_menu):
    from app.domain.menu import is_valid_option_in_category

    assert is_valid_option_in_category("엑스트라", "사이즈") is True
    assert is_valid_option_in_category("샷추가", "샷추가") is True
    assert is_valid_option_in_category("초콜릿시럽", "시럽추가") is False
    assert is_valid_option_in_category("벤티", "사이즈") is False  # 드롭됨


def test_find_option_category(loaded_menu):
    """옵션 이름만 알 때 카테고리 자동 판별."""
    from app.domain.menu import find_option_category

    assert find_option_category("엑스트라") == "사이즈"
    assert find_option_category("샷추가") == "샷추가"
    assert find_option_category("헤이즐넛시럽 추가") == "시럽추가"
    assert find_option_category("초콜릿시럽") is None  # 매장에 없는 옵션


def test_option_applicable_to_category(loaded_menu):
    """샷추가는 coffee/cold_brew/decaf에만 적용 가능."""
    from app.domain.menu import is_option_applicable

    assert is_option_applicable(option_kr="샷추가", menu_category="coffee") is True
    assert is_option_applicable(option_kr="샷추가", menu_category="bakery") is False
    # 사이즈는 모든 메뉴에 적용
    assert is_option_applicable(option_kr="엑스트라", menu_category="bakery") is True
