"""tool_name → Cart 메서드 라우팅 + 도메인 검증."""
from __future__ import annotations

from typing import Any, Dict, List

from app.domain.cart import Cart
from app.domain.menu import (
    find_option_category,
    find_similar_menus,
    get_menu_category,
    get_menu_stock,
    is_option_applicable,
    is_valid_menu,
    resolve_menu_alias,
)


def _check_stock(cart: Cart, menu_kr: str, additional_quantity: int) -> Dict[str, Any] | None:
    """add_menu 또는 replace_menu 처리 전 stock 검증.

    cart에 이미 담긴 수량 + 새로 요청한 수량이 재고 초과면 INSUFFICIENT_STOCK 반환.
    충분하거나 무한 재고면 None.
    """
    stock = get_menu_stock(menu_kr)
    if stock is None:
        return None  # 무한 재고

    in_cart = sum(it["quantity"] for it in cart.snapshot() if it["menu"] == menu_kr)
    if stock == 0:
        return {
            "status": "SOLD_OUT",
            "menu": menu_kr,
            "available": 0,
            "error_detail": f"{menu_kr}는 오늘 품절되었어요",
        }
    if in_cart + additional_quantity > stock:
        return {
            "status": "INSUFFICIENT_STOCK",
            "menu": menu_kr,
            "available": stock - in_cart,
            "already_in_cart": in_cart,
            "error_detail": f"{menu_kr}는 {stock - in_cart}개까지만 주문 가능해요",
        }
    return None


def _validate_options(menu_kr: str, options: List[str]) -> Dict[str, Any] | None:
    """옵션 유효성 검사. 문제 있으면 INVALID_OPTION result 반환, 없으면 None."""
    if not options:
        return None
    menu_category = get_menu_category(menu_kr)
    for opt in options:
        cat = find_option_category(opt)
        if cat is None:
            return {
                "status": "INVALID_OPTION",
                "menu": menu_kr,
                "error_detail": f"매장에 없는 옵션: {opt}",
            }
        if menu_category and not is_option_applicable(opt, menu_category):
            return {
                "status": "INVALID_OPTION",
                "menu": menu_kr,
                "error_detail": f"{menu_kr}에는 {opt} 옵션을 적용할 수 없어요",
            }
    return None


def _add_menu(cart: Cart, args: Dict[str, Any]) -> Dict[str, Any]:
    menu = args.get("menu", "")
    menu = resolve_menu_alias(menu) or menu  # 모델이 줄임말('아아' 등)을 그대로 넘긴 경우 보정
    quantity = int(args.get("quantity", 1))
    options = list(args.get("options") or [])

    if not is_valid_menu(menu):
        # P2 mitigation: 비슷한 메뉴 후보 제시 (모델이 사용자에게 안내할 수 있도록)
        suggestions = find_similar_menus(menu, max_k=3)
        return {
            "status": "INVALID_MENU",
            "menu": menu,
            "error_detail": f"매장에서 취급하지 않는 메뉴: {menu}",
            "suggestions": suggestions,
        }
    opt_err = _validate_options(menu, options)
    if opt_err:
        return opt_err

    stock_err = _check_stock(cart, menu, quantity)
    if stock_err:
        return stock_err

    return cart.add(menu=menu, quantity=quantity, options=options)


def _remove_menu(cart: Cart, args: Dict[str, Any]) -> Dict[str, Any]:
    menu = args.get("menu", "")
    menu = resolve_menu_alias(menu) or menu  # 줄임말 보정
    quantity = int(args.get("quantity", -1))
    if menu != "ALL" and not is_valid_menu(menu):
        # menu가 'ALL'이 아니고 사전에도 없으면 not_in_cart 처리 (사용자가 이상한 이름 부른 경우)
        return {"status": "MENU_NOT_IN_CART", "menu": menu}
    return cart.remove(menu=menu, quantity=quantity)


def _replace_menu(cart: Cart, args: Dict[str, Any]) -> Dict[str, Any]:
    from_menu = args.get("from_menu", "")
    from_menu = resolve_menu_alias(from_menu) or from_menu  # 줄임말 보정
    to_menu = args.get("to_menu", "")
    to_menu = resolve_menu_alias(to_menu) or to_menu  # 줄임말 보정
    to_options = list(args.get("to_options") or [])

    if not is_valid_menu(to_menu):
        return {
            "status": "INVALID_MENU",
            "menu": to_menu,
            "error_detail": f"매장에서 취급하지 않는 메뉴: {to_menu}",
        }
    opt_err = _validate_options(to_menu, to_options)
    if opt_err:
        return opt_err

    # replace 시점에는 from_menu 수량만큼 to_menu가 들어가므로 그만큼 stock 체크
    from_item = next((it for it in cart.snapshot() if it["menu"] == from_menu), None)
    qty_to_check = from_item["quantity"] if from_item else 1
    stock_err = _check_stock(cart, to_menu, qty_to_check)
    if stock_err:
        return stock_err

    return cart.replace(from_menu=from_menu, to_menu=to_menu, to_options=to_options)


# 같은 카테고리 내에서 하나만 유효한(상호배타) 옵션 카테고리.
# 새 옵션이 들어오면 같은 카테고리의 기존 옵션을 대체한다.
_EXCLUSIVE_OPT_CATEGORIES = {"사이즈", "휘핑선택", "당도선택", "얼음선택"}


def _merge_options(existing: List[str], new: List[str]) -> List[str]:
    """기존 옵션에 새 옵션을 병합.

    - 상호배타 카테고리(사이즈/휘핑/당도/얼음): 새 값이 같은 카테고리 기존 값을 대체.
    - 누적 가능 카테고리(샷추가/시럽추가): 단순 추가(중복 제거).
    모델이 '샷 추가'만 보내도 기존 '엑스트라'가 사라지지 않게 한다.
    """
    result = list(existing)
    for opt in new:
        cat = find_option_category(opt)
        if cat in _EXCLUSIVE_OPT_CATEGORIES:
            result = [o for o in result if find_option_category(o) != cat]
        if opt not in result:
            result.append(opt)
    return result


def _change_option(cart: Cart, args: Dict[str, Any]) -> Dict[str, Any]:
    menu = args.get("menu", "")
    menu = resolve_menu_alias(menu) or menu  # 줄임말 보정
    new_options = list(args.get("new_options") or [])
    opt_err = _validate_options(menu, new_options) if is_valid_menu(menu) else None
    if opt_err:
        return opt_err

    # new_options가 비어 있으면 '옵션 전체 해제' 의도 — 그대로 빈 리스트로 설정.
    # 비어 있지 않으면 기존 옵션과 병합해 의도치 않은 옵션 소실을 막는다.
    if not new_options:
        merged = []
    else:
        current = next(
            (it["options"] for it in cart.snapshot() if it["menu"] == menu), []
        )
        merged = _merge_options(current, new_options)
    return cart.change_option(menu=menu, new_options=merged)


def _check_cart(cart: Cart, args: Dict[str, Any]) -> Dict[str, Any]:
    if cart.is_empty():
        return {"status": "EMPTY_CART", "cart": []}
    return {"status": "CART_VIEW", "cart": cart.snapshot()}


def _inquire_menu_info(cart: Cart, args: Dict[str, Any]) -> Dict[str, Any]:
    """메뉴 정보 질의 응답.

    Tool result 설계 (L3 처치):
    - candidates를 압축된 list로 표시 (kr + 가격만, score는 모델에 노이즈)
    - `next_action_hint`로 actor instruction inline 박제:
      "이번 답변 후 사용자가 메뉴 명시하면 add_menu 호출. inquire 또 호출하지 말 것."
    - 이렇게 하면 모델이 inquiry 모드에 stuck 되지 않고 다음 turn에 add로 라우팅.
    """
    question = args.get("question", "")
    try:
        from app.llm.rag import answer_menu_inquiry

        rag = answer_menu_inquiry(question, k=5)
        candidates = rag.get("candidates", [])
        # 압축: score 제거, list 형식
        compact = [f"{c['kr']} ({c['price_l']}원)" for c in candidates[:5]]

        return {
            "status": "MENU_INQUIRY",
            "candidates": compact,
            "next_action_hint": (
                "이 답변 후 사용자가 구체적인 메뉴를 말하면 inquire_menu_info를 다시 호출하지 말고 "
                "add_menu를 호출하세요. '그럼/그러면/그걸로' 같은 연결어 뒤에 메뉴가 오면 주문 의도예요."
            ),
        }
    except Exception as exc:  # pragma: no cover — embedding 모델 미설치 fallback
        return {
            "status": "MENU_INQUIRY",
            "candidates": [],
            "next_action_hint": "RAG 미가용. 사용자에게 메뉴 카테고리만 안내.",
        }


def _done(cart: Cart, args: Dict[str, Any]) -> Dict[str, Any]:
    if cart.is_empty():
        return {"status": "EMPTY_CART", "cart": []}
    return {"status": "ORDER_COMPLETED", "cart": cart.snapshot()}


_HANDLERS = {
    "add_menu": _add_menu,
    "remove_menu": _remove_menu,
    "replace_menu": _replace_menu,
    "change_option": _change_option,
    "check_cart": _check_cart,
    "inquire_menu_info": _inquire_menu_info,
    "done": _done,
}


def dispatch_tool(name: str, args: Dict[str, Any], cart: Cart) -> Dict[str, Any]:
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"status": "UNKNOWN_TOOL", "tool_name": name}
    return handler(cart, args)
