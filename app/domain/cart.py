"""In-memory 카트. 도메인 검증 없음 (검증은 dispatcher에서 처리)."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class CartItem:
    menu: str
    quantity: int
    options: List[str] = field(default_factory=list)

    def same_as(self, menu: str, options: List[str]) -> bool:
        return self.menu == menu and sorted(self.options) == sorted(options)

    def to_dict(self) -> Dict[str, Any]:
        return {"menu": self.menu, "quantity": self.quantity, "options": list(self.options)}


class Cart:
    def __init__(self) -> None:
        self._items: List[CartItem] = []

    # ----- read -----
    def is_empty(self) -> bool:
        return len(self._items) == 0

    def snapshot(self) -> List[Dict[str, Any]]:
        return [copy.deepcopy(it.to_dict()) for it in self._items]

    # ----- write -----
    def add(self, menu: str, quantity: int, options: List[str]) -> Dict[str, Any]:
        for it in self._items:
            if it.same_as(menu, options):
                it.quantity += quantity
                return {
                    "status": "INCREMENTED",
                    "menu": menu,
                    "quantity": it.quantity,
                    "options": list(it.options),
                }

        item = CartItem(menu=menu, quantity=quantity, options=list(options))
        self._items.append(item)
        return {
            "status": "ADDED",
            "menu": menu,
            "quantity": quantity,
            "options": list(options),
        }

    def remove(self, menu: str, quantity: int) -> Dict[str, Any]:
        if menu == "ALL":
            self._items.clear()
            return {"status": "REMOVED", "menu": "ALL"}

        for idx, it in enumerate(self._items):
            if it.menu == menu:
                if quantity == -1 or quantity >= it.quantity:
                    self._items.pop(idx)
                    return {"status": "REMOVED", "menu": menu, "quantity": 0}
                it.quantity -= quantity
                return {"status": "REMOVED", "menu": menu, "quantity": it.quantity}

        return {"status": "MENU_NOT_IN_CART", "menu": menu}

    def replace(
        self, from_menu: str, to_menu: str, to_options: List[str]
    ) -> Dict[str, Any]:
        for it in self._items:
            if it.menu == from_menu:
                it.menu = to_menu
                it.options = list(to_options)
                return {
                    "status": "REPLACED",
                    "from_menu": from_menu,
                    "menu": to_menu,
                    "quantity": it.quantity,
                    "options": list(to_options),
                }
        return {"status": "MENU_NOT_IN_CART", "menu": from_menu}

    def change_option(self, menu: str, new_options: List[str]) -> Dict[str, Any]:
        for it in self._items:
            if it.menu == menu:
                it.options = list(new_options)
                return {
                    "status": "OPTION_CHANGED",
                    "menu": menu,
                    "options": list(new_options),
                }
        return {"status": "MENU_NOT_IN_CART", "menu": menu}

    def clear(self) -> None:
        self._items.clear()
