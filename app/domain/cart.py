"""In-memory 카트 + 변경 이력.

- 도메인 검증은 dispatcher에서 (Cart 자체는 데이터 + 이력만).
- 매 write 메서드는 직전 snapshot을 _history에 기록 → undo() 한 단계 복원.
- _history는 N개 LIFO(기본 20). 메모리 폭증 방지.
"""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_HISTORY_LIMIT = 20


@dataclass
class CartItem:
    menu: str
    quantity: int
    options: List[str] = field(default_factory=list)

    def same_as(self, menu: str, options: List[str]) -> bool:
        return self.menu == menu and sorted(self.options) == sorted(options)

    def to_dict(self) -> Dict[str, Any]:
        return {"menu": self.menu, "quantity": self.quantity, "options": list(self.options)}


@dataclass
class CartEvent:
    """카트 변경 1건 — undo + 디버그 패널에서 사용."""

    action: str               # "add" | "remove" | "replace" | "change_option" | "clear"
    summary: str              # 사람 읽을 수 있는 한 줄 ("아이스아메리카노 1잔 담음")
    snapshot_before: List[Dict[str, Any]]
    snapshot_after: List[Dict[str, Any]]
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "summary": self.summary,
            "before": self.snapshot_before,
            "after": self.snapshot_after,
            "timestamp": self.timestamp,
        }


class Cart:
    def __init__(self) -> None:
        self._items: List[CartItem] = []
        self._history: List[CartEvent] = []

    # ----- read -----
    def is_empty(self) -> bool:
        return len(self._items) == 0

    def snapshot(self) -> List[Dict[str, Any]]:
        return [copy.deepcopy(it.to_dict()) for it in self._items]

    def history_snapshot(self) -> List[Dict[str, Any]]:
        """디버그 / undo 가시화용 — 가장 최근 변경이 마지막."""
        return [ev.to_dict() for ev in self._history]

    # ----- internal -----
    def _record(self, action: str, summary: str, snapshot_before: List[Dict[str, Any]]) -> None:
        ev = CartEvent(
            action=action,
            summary=summary,
            snapshot_before=snapshot_before,
            snapshot_after=self.snapshot(),
        )
        self._history.append(ev)
        if len(self._history) > _HISTORY_LIMIT:
            self._history = self._history[-_HISTORY_LIMIT:]

    def _restore(self, snap: List[Dict[str, Any]]) -> None:
        self._items = [
            CartItem(menu=it["menu"], quantity=it["quantity"], options=list(it.get("options") or []))
            for it in snap
        ]

    # ----- write -----
    def add(self, menu: str, quantity: int, options: List[str]) -> Dict[str, Any]:
        before = self.snapshot()
        for it in self._items:
            if it.same_as(menu, options):
                it.quantity += quantity
                self._record("add", f"{menu} {quantity}잔 추가 (누적 {it.quantity}잔)", before)
                return {
                    "status": "INCREMENTED",
                    "menu": menu,
                    "quantity": it.quantity,
                    "options": list(it.options),
                }

        item = CartItem(menu=menu, quantity=quantity, options=list(options))
        self._items.append(item)
        self._record("add", f"{menu} {quantity}잔 담음", before)
        return {
            "status": "ADDED",
            "menu": menu,
            "quantity": quantity,
            "options": list(options),
        }

    def remove(self, menu: str, quantity: int) -> Dict[str, Any]:
        before = self.snapshot()
        if menu == "ALL":
            had = len(self._items) > 0
            self._items.clear()
            if had:
                self._record("clear", "카트 전체 비움", before)
            return {"status": "REMOVED", "menu": "ALL"}

        for idx, it in enumerate(self._items):
            if it.menu == menu:
                if quantity == -1 or quantity >= it.quantity:
                    self._items.pop(idx)
                    self._record("remove", f"{menu} 전체 제거", before)
                    return {"status": "REMOVED", "menu": menu, "quantity": 0}
                it.quantity -= quantity
                self._record("remove", f"{menu} {quantity}잔 줄임 (잔여 {it.quantity}잔)", before)
                return {"status": "REMOVED", "menu": menu, "quantity": it.quantity}

        return {"status": "MENU_NOT_IN_CART", "menu": menu}

    def replace(
        self, from_menu: str, to_menu: str, to_options: List[str]
    ) -> Dict[str, Any]:
        before = self.snapshot()
        for it in self._items:
            if it.menu == from_menu:
                it.menu = to_menu
                it.options = list(to_options)
                self._record("replace", f"{from_menu} → {to_menu}", before)
                return {
                    "status": "REPLACED",
                    "from_menu": from_menu,
                    "menu": to_menu,
                    "quantity": it.quantity,
                    "options": list(to_options),
                }
        return {"status": "MENU_NOT_IN_CART", "menu": from_menu}

    def change_option(self, menu: str, new_options: List[str]) -> Dict[str, Any]:
        before = self.snapshot()
        for it in self._items:
            if it.menu == menu:
                it.options = list(new_options)
                opt_str = ", ".join(new_options) if new_options else "옵션 없음"
                self._record("change_option", f"{menu} 옵션 → {opt_str}", before)
                return {
                    "status": "OPTION_CHANGED",
                    "menu": menu,
                    "options": list(new_options),
                }
        return {"status": "MENU_NOT_IN_CART", "menu": menu}

    def clear(self) -> None:
        before = self.snapshot()
        had = len(self._items) > 0
        self._items.clear()
        if had:
            self._record("clear", "카트 전체 비움", before)

    def undo(self) -> Dict[str, Any]:
        """가장 최근 변경 1건 되돌리기.

        Returns:
            {status: "UNDONE", restored_action: "...", restored_summary: "..."}
            또는 {status: "NOTHING_TO_UNDO"}.
        """
        if not self._history:
            return {"status": "NOTHING_TO_UNDO"}
        ev = self._history.pop()
        self._restore(ev.snapshot_before)
        return {
            "status": "UNDONE",
            "restored_action": ev.action,
            "restored_summary": ev.summary,
        }
