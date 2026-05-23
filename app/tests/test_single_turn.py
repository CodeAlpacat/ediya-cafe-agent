"""Phase 2 RED: 7개 도구 × 골든 발화 1개씩 (Ollama 라이브).

각 테스트는:
1. Gemma 4 E2B에 발화 전달
2. 기대한 tool name이 호출되는지 검증
3. (도구별) 기대 인자 부분 일치 검증

Ollama가 없으면 fixture가 skip 처리.
"""
import json
from typing import Any, Dict, List

import pytest

pytestmark = pytest.mark.ollama


def _call(client, model: str, user_msg: str) -> Dict[str, Any]:
    """단일턴 호출 — chat_once 헬퍼. agent.py 구현 전까지 ImportError로 RED 유지."""
    from app.llm.agent import chat_once, AgentConfig

    cfg = AgentConfig(model=model)
    return chat_once(client=client, user_message=user_msg, config=cfg)


def _extract_tool_calls(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """chat_once 반환에서 tool calls 목록 추출.
    Expected shape: {"tool_calls": [{"name": ..., "arguments": {...}}, ...], "response_text": str}
    """
    return result.get("tool_calls", [])


# ============ 1. add_menu ============

def test_add_menu_simple(ollama_client, model_name):
    result = _call(ollama_client, model_name, "아이스 아메리카노 한 잔 주세요.")
    calls = _extract_tool_calls(result)

    assert len(calls) >= 1
    names = [c["name"] for c in calls]
    assert "add_menu" in names

    add_call = next(c for c in calls if c["name"] == "add_menu")
    args = add_call["arguments"]
    assert args["menu"] == "아이스아메리카노"
    assert int(args["quantity"]) == 1


def test_add_menu_with_quantity(ollama_client, model_name):
    result = _call(ollama_client, model_name, "따뜻한 바닐라라떼 두 잔 주세요.")
    calls = _extract_tool_calls(result)
    add_call = next(c for c in calls if c["name"] == "add_menu")

    assert add_call["arguments"]["menu"] == "핫바닐라라떼"
    assert int(add_call["arguments"]["quantity"]) == 2


# ----- helper: 카트에 메뉴가 있는 상태의 history 만들기 -----

def _history_with_americano_in_cart() -> List[Dict[str, Any]]:
    """이전 턴에서 '아이스아메리카노 1잔'을 카트에 담은 상태로 history 구성."""
    from app.llm.prompts import SYSTEM_PROMPT

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "아이스 아메리카노 한 잔 주세요."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_seed_1",
                    "type": "function",
                    "function": {
                        "name": "add_menu",
                        "arguments": json.dumps(
                            {"menu": "아이스아메리카노", "quantity": 1, "options": []},
                            ensure_ascii=False,
                        ),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_seed_1",
            "content": json.dumps(
                {"status": "ADDED", "menu": "아이스아메리카노", "quantity": 1, "options": []},
                ensure_ascii=False,
            ),
        },
        {"role": "assistant", "content": "아이스 아메리카노 한 잔 담았어요. 더 필요한 것 있어요?"},
    ]


# ============ 2. remove_menu ============

def test_remove_menu(ollama_client, model_name):
    """카트에 아이스아메리카노가 있는 상태에서 'remove' 발화."""
    from app.llm.agent import chat_once, AgentConfig

    cfg = AgentConfig(model=model_name)
    result = chat_once(
        client=ollama_client,
        user_message="방금 주문한 아이스 아메리카노 빼주세요.",
        config=cfg,
        history=_history_with_americano_in_cart(),
    )
    names = [c["name"] for c in _extract_tool_calls(result)]
    assert "remove_menu" in names


# ============ 3. replace_menu ============

def test_replace_menu(ollama_client, model_name):
    """카트에 아이스아메리카노가 있는 상태에서 'swap' 발화."""
    from app.llm.agent import chat_once, AgentConfig

    cfg = AgentConfig(model=model_name)
    result = chat_once(
        client=ollama_client,
        user_message="아메리카노 말고 아이스 카페라떼로 바꿔주세요.",
        config=cfg,
        history=_history_with_americano_in_cart(),
    )
    names = [c["name"] for c in _extract_tool_calls(result)]
    assert "replace_menu" in names

    rep = next(c for c in _extract_tool_calls(result) if c["name"] == "replace_menu")
    args = rep["arguments"]
    assert args["from_menu"] == "아이스아메리카노"
    assert args["to_menu"] == "아이스카페라떼"


# ============ 4. change_option ============

def test_change_option(ollama_client, model_name):
    """카트에 아이스아메리카노가 있는 상태에서 옵션 변경 발화."""
    from app.llm.agent import chat_once, AgentConfig

    cfg = AgentConfig(model=model_name)
    result = chat_once(
        client=ollama_client,
        user_message="엑스트라 사이즈로 변경해주세요.",
        config=cfg,
        history=_history_with_americano_in_cart(),
    )
    names = [c["name"] for c in _extract_tool_calls(result)]
    assert "change_option" in names

    change = next(c for c in _extract_tool_calls(result) if c["name"] == "change_option")
    assert "엑스트라" in change["arguments"]["new_options"]


# ============ 5. check_cart ============

def test_check_cart(ollama_client, model_name):
    result = _call(ollama_client, model_name, "지금 제 주문 뭐 있는지 알려주세요.")
    names = [c["name"] for c in _extract_tool_calls(result)]
    assert "check_cart" in names


# ============ 6. inquire_menu_info ============

def test_inquire_menu_info(ollama_client, model_name):
    result = _call(ollama_client, model_name, "단 거 추천해주세요.")
    names = [c["name"] for c in _extract_tool_calls(result)]
    assert "inquire_menu_info" in names


# ============ 7. done ============

def test_done(ollama_client, model_name):
    result = _call(ollama_client, model_name, "주문 끝낼게요.")
    names = [c["name"] for c in _extract_tool_calls(result)]
    assert "done" in names


# ============ Bonus: Ediya 특이 규칙 — 디카페인 분기 ============

def test_decaf_routes_to_cold_brew_menu(ollama_client, model_name):
    """이디야는 디카페인이 콜드브루 별도 메뉴. 'add_menu(디카페인콜드브루아메리카노)'로 호출돼야 함."""
    result = _call(ollama_client, model_name, "디카페인 아메리카노 주세요.")
    calls = _extract_tool_calls(result)
    names = [c["name"] for c in calls]

    if "add_menu" in names:
        add = next(c for c in calls if c["name"] == "add_menu")
        # 디카페인 콜드브루 계열 메뉴여야 함
        assert "디카페인" in add["arguments"]["menu"]
        assert "콜드브루" in add["arguments"]["menu"]
