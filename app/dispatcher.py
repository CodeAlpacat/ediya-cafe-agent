"""tool_name → Cart 메서드 라우팅 + 도메인 검증."""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from app.domain.cart import Cart
from app.domain.menu import (
    find_option_category,
    find_similar_menus,
    get_menu_category,
    get_menu_stock,
    is_option_applicable,
    is_valid_menu,
    keyword_menu_search,
    load_menu,
    resolve_menu_alias,
)

logger = logging.getLogger("ediya.dispatcher")


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


def _category_breakdown_lines() -> List[str]:
    """카테고리별 대표 메뉴를 한 줄씩 — RAG/키워드 검색이 비어도 항상 사실 기반 후보 제공.

    "케이크/스콘" 같은 환각 fallback을 막기 위한 마지막 그라운딩 재료.
    """
    data = load_menu()
    cats = data.get("categories", {})
    by_cat: Dict[str, List[str]] = {}
    for m in data["menus"]:
        by_cat.setdefault(m.get("category", ""), []).append(m["kr"])
    lines = []
    for cid, c in cats.items():
        examples = by_cat.get(cid, [])[:3]
        if not examples:
            continue
        desc = c.get("descriptor", cid)
        lines.append(f"{desc} - 예: {', '.join(examples)}")
    return lines


def _inquire_menu_info(cart: Cart, args: Dict[str, Any]) -> Dict[str, Any]:
    """메뉴 정보 질의 응답.

    그라운딩 사다리 (전부 사실 기반 후보를 모델에 전달 → 환각 방지):
      1) 임베딩 RAG (의미 매칭)
      2) 키워드 매칭 (RAG 미가용/빈 결과 시)
      3) 카테고리 breakdown (그래도 비면 — 매장 전체 카테고리 안내)
    + answer_constraint로 'candidates 밖 메뉴 언급 금지'를 명시.
    """
    question = args.get("question", "")
    candidates: List[str] = []

    # 1) 임베딩 RAG
    try:
        from app.llm.rag import answer_menu_inquiry

        rag = answer_menu_inquiry(question, k=5)
        candidates = [
            f"{c['kr']} ({c['price_l']}원)" for c in (rag.get("candidates") or [])
        ]
    except Exception as exc:  # pragma: no cover — embedding 모델 미설치 등
        logger.warning("RAG 미가용 — 키워드 검색으로 폴백: %s", exc)

    # 2) 키워드 폴백
    if not candidates:
        hits = keyword_menu_search(question, max_k=5)
        candidates = [f"{h['kr']} ({h.get('base_price_l', 0) or 0}원)" for h in hits]

    # 3) 카테고리 breakdown — 그래도 비면 매장 전체 안내
    if not candidates:
        candidates = _category_breakdown_lines()

    return {
        "status": "MENU_INQUIRY",
        "candidates": candidates,
        "answer_constraint": (
            "candidates 안에 적힌 메뉴/카테고리만 사실로 인정해라. "
            "그 밖의 항목(예: 케이크, 스콘)을 답변에 지어내면 손님 신뢰가 깨진다.\n"
            "손님 질문과 연관된 항목이 candidates에 있으면(예: '디저트' 질문 → "
            "베이커리·빙수 카테고리가 후보에 있음) 그 항목들을 적극적으로 안내해라. "
            "카테고리 설명에 딸린 '예: …' 메뉴들을 손님에게 그대로 전달해도 좋다.\n"
            "정말로 candidates 전체에서 손님 질문과 닿는 게 하나도 없을 때만 "
            "'해당 종류는 없어요'라고 답해라."
        ),
        "next_action_hint": (
            "이 답변 후 사용자가 구체적인 메뉴를 말하면 inquire_menu_info를 다시 호출하지 말고 "
            "add_menu를 호출하세요. '그럼/그러면/그걸로' 같은 연결어 뒤에 메뉴가 오면 주문 의도예요."
        ),
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
