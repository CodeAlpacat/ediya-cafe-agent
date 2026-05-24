"""IntentProposal — NLU 결과 컨테이너.

모델에게 system message로 inject되는 표준 형식. 결정론적이라 단위 테스트로 100% 검증.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Literal, Optional

ActionHint = Literal[
    "add",            # add_menu
    "remove",         # remove_menu
    "replace",        # replace_menu
    "change_option",  # change_option
    "check_cart",     # check_cart
    "inquire",        # inquire_menu_info
    "done",           # done
    "undo",           # undo
    "ambiguous",      # 사용자 의도 자체가 모호 — 모델이 되묻기
    "unknown",        # NLU가 판단 보류 — 모델에게 위임
]


AmbiguityKind = Literal[
    "temperature",      # 아이스/핫 누락
    "menu_family",      # "라떼" / "커피" 같은 가족명
    "menu_unknown",     # 매장에 없는 메뉴 추정
    "option_missing",   # 옵션 사전에 없음 (두유/오트밀크 등)
    "decaf_hot",        # 디카페인 핫 요청 (이디야 룰 위반)
]


@dataclass
class MenuIntent:
    """1개 메뉴 의도. 복합 발화면 여러 개."""
    menu_kr: Optional[str] = None              # 확정 메뉴 (정규화된 정식명). None이면 모호.
    quantity: int = 1
    options: List[str] = field(default_factory=list)
    missing_options: List[str] = field(default_factory=list)
    ambiguity: Optional[AmbiguityKind] = None
    ambiguity_candidates: List[str] = field(default_factory=list)  # 모호일 때 후보 메뉴들
    notes: List[str] = field(default_factory=list)                 # NLU 가공 메모

    def is_resolved(self) -> bool:
        return self.menu_kr is not None and self.ambiguity is None


@dataclass
class IntentProposal:
    """1턴 발화 → 표준 의도 분석 결과."""
    action: ActionHint
    intents: List[MenuIntent] = field(default_factory=list)
    raw_utterance: str = ""
    global_notes: List[str] = field(default_factory=list)   # 전체 발화 수준 메모

    def is_fully_resolved(self) -> bool:
        return bool(self.intents) and all(i.is_resolved() for i in self.intents)
