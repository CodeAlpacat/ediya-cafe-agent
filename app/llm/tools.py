"""OpenAI Function Call schema — 7 tools for Ediya coffee agent.

설계 결정 (2026-05-21):
- description은 영어 위주 + 한국어 트리거 예시 inline.
  → Gemma 4 E2B는 영어 학습량이 압도적. description을 영어로 두면 한국어 발화의 도구 라우팅이 안정.
- 각 설명에 "다른 도구와의 차이" 명시 — E2B의 도구 혼동 차단.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_menu",
            "description": (
                "Add a new menu item to the cart, or increase quantity of an existing item. "
                "Always call this function (do NOT just reply with text) when the user expresses intent to order or add. "
                "Korean trigger examples: "
                "'아이스 아메리카노 한 잔 주세요' / "
                "'바닐라 라떼 두 잔 더 주세요' / "
                "'카페모카 한 잔 추가' / "
                "'그럼 아이스 카페모카 주세요' (after inquiry, conjunction '그럼/그래서/그러면' followed by menu = order intent) / "
                "'그걸로 주세요' (referring to previously mentioned menu). "
                "If the same menu+options already exists in cart, quantity will auto-increment."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "menu": {
                        "type": "string",
                        "description": (
                            "Menu name. Temperature (아이스/핫) is part of the name. "
                            "Examples: '아이스아메리카노', '핫카페라떼', '디카페인콜드브루아메리카노'."
                        ),
                    },
                    "quantity": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Quantity. Default 1 if not specified.",
                    },
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "List of options. Empty array [] if none. "
                            "Available options: '엑스트라' (size), '샷추가', "
                            "'헤이즐넛시럽 추가', '바닐라시럽 추가', '카라멜시럽 추가', '흑당시럽 추가', "
                            "'휘핑추가', '휘핑빼기', '저당', '달게', '덜달게'."
                        ),
                    },
                },
                "required": ["menu", "quantity", "options"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_menu",
            "description": (
                "Remove a menu item from cart or decrease its quantity. "
                "Always call this function (do NOT just reply with text) when the user asks to remove/decrease/cancel anything. "
                "Korean trigger examples: "
                "'아메리카노 빼주세요' (remove entirely, quantity=-1) / "
                "'바닐라라떼 한 잔만 빼주세요' (decrease by 1, quantity=1) / "
                "'한 잔 줄여주세요' (decrease by 1) / "
                "'하나 취소' / "
                "'전부 취소해주세요' (menu='ALL', quantity=-1). "
                "If user wants to swap one menu for another, use replace_menu instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "menu": {
                        "type": "string",
                        "description": "Menu name to remove. Use 'ALL' to clear the entire cart.",
                    },
                    "quantity": {
                        "type": "integer",
                        "description": "Quantity to remove. Use -1 to remove all of that menu.",
                    },
                },
                "required": ["menu", "quantity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_menu",
            "description": (
                "Swap one menu item in cart for a different menu item. Use ONLY when the menu itself changes. "
                "Korean trigger examples: "
                "'아메리카노 말고 라떼로 바꿔주세요' / "
                "'카페모카로 변경해주세요' / "
                "'그거 말고 콜드브루로'. "
                "For changing options of the same menu, use change_option. "
                "For adding a new menu (not swapping), use add_menu."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "from_menu": {"type": "string", "description": "Existing menu name to replace"},
                    "to_menu": {"type": "string", "description": "New menu name"},
                    "to_options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Options for the new menu. Empty array [] if none.",
                    },
                },
                "required": ["from_menu", "to_menu", "to_options"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "change_option",
            "description": (
                "Change options of a menu already in cart. Menu itself stays the same. "
                "Korean trigger examples: "
                "'엑스트라 사이즈로 변경' / "
                "'샷 추가해주세요' / "
                "'휘핑 빼주세요' / "
                "'바닐라 시럽 추가'. "
                "If the menu itself changes, use replace_menu. If adding a new item, use add_menu."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "menu": {"type": "string", "description": "Menu name whose options to change"},
                    "new_options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Options the user is adding/changing right now. "
                            "Just pass the newly requested option(s) — existing unrelated "
                            "options are kept automatically. Size/whip/sweetness/ice are "
                            "mutually exclusive, so a new value there replaces the old one. "
                            "Pass an empty array [] only to clear all options."
                        ),
                    },
                },
                "required": ["menu", "new_options"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_cart",
            "description": (
                "Show current cart contents. "
                "Korean trigger examples: "
                "'지금 뭐 시켰지' / "
                "'주문 내역 보여주세요' / "
                "'카트 확인'. "
                "Do NOT call this right after add/remove/change — those tools already return cart info."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inquire_menu_info",
            "description": (
                "Answer questions about menu (price, taste, recommendation). "
                "Korean trigger examples: "
                "'아메리카노 얼마예요?' / "
                "'단 거 추천해주세요' / "
                "'디카페인 있어요?'. "
                "Use only when user asks for info, not when they want to order."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Original user question"},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": (
                "Finalize order. "
                "Korean trigger examples: "
                "'주문 끝낼게요' / "
                "'결제할게요' / "
                "'여기까지 할게요' / "
                "'끝.' "
                "Can be called even when cart is empty."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "undo",
            "description": (
                "Undo the most recent cart change (add/remove/replace/change_option). "
                "Use when the user explicitly wants to revert their last action. "
                "Korean trigger examples: "
                "'아까 한 거 되돌려' / "
                "'방금 취소한 거 다시 살려' / "
                "'원래대로 해주세요' / "
                "'되돌리기' / "
                "'아 잘못 말했어요'. "
                "Do NOT call this for a NEW removal request — use remove_menu instead. "
                "Returns NOTHING_TO_UNDO if there is no recorded change."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]
