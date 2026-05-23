"""Phase 3 RED: 멀티턴 시나리오 검증.

각 시나리오:
- run_turn으로 발화 1개씩 처리
- 턴마다 expected tool이 호출됐는지 확인 (history inspection)
- 최종 cart 상태가 기대값과 일치하는지 확인
- 응답 텍스트에 금지 표현 없음 (따옴표, JSON 등)

Ollama 라이브 필요. seed=4242로 의사-결정론.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

pytestmark = pytest.mark.ollama


# ---------- helpers ----------


def _tools_used(history: List[Dict[str, Any]]) -> List[str]:
    """history에 호출된 tool 이름 목록 (assistant role의 tool_calls에서 추출)."""
    used = []
    for msg in history:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                used.append(tc["function"]["name"])
    return used


def _run_turns(client, model: str, user_messages: List[str]):
    """주어진 발화 list를 순서대로 run_turn으로 처리. (cart, history, responses) 반환."""
    from app.llm.agent import AgentConfig, run_turn
    from app.domain.cart import Cart

    cart = Cart()
    history: List[Dict[str, Any]] = []
    responses: List[str] = []
    cfg = AgentConfig(model=model)

    for msg in user_messages:
        text = run_turn(client=client, user_message=msg, cart=cart, history=history, config=cfg)
        responses.append(text)
    return cart, history, responses


def _no_forbidden_tokens(text: str) -> bool:
    """응답에 따옴표/함수명/JSON 노출 금지."""
    forbidden = ["{", "}", "add_menu", "remove_menu", "replace_menu", "change_option",
                 "check_cart", "inquire_menu_info", "tool_call"]
    return not any(tok in text for tok in forbidden)


# ============ 시나리오 A: 6턴 happy path ============


def test_scenario_a_full_order_flow(ollama_client, model_name):
    """주문 → 옵션 변경 → 추가 → 조회 → 일부 제거 → 완료."""
    turns = [
        "아이스 아메리카노 한 잔 주세요.",                  # add_menu
        "엑스트라 사이즈로 변경해주세요.",                 # change_option
        "따뜻한 바닐라라떼도 두 잔 추가해주세요.",         # add_menu
        "지금 주문 뭐 있어요?",                            # check_cart
        "바닐라라떼 한 잔만 빼주세요.",                    # remove_menu
        "주문 끝낼게요.",                                  # done
    ]
    cart, history, responses = _run_turns(ollama_client, model_name, turns)

    used = _tools_used(history)
    assert "add_menu" in used
    assert "change_option" in used
    assert "check_cart" in used
    assert "remove_menu" in used
    assert "done" in used

    # 최종 카트: 아이스아메리카노 (엑스트라) 1잔 + 핫바닐라라떼 1잔
    snap = cart.snapshot()
    assert len(snap) == 2

    menus = {item["menu"] for item in snap}
    assert "아이스아메리카노" in menus
    assert "핫바닐라라떼" in menus

    americano = next(it for it in snap if it["menu"] == "아이스아메리카노")
    assert "엑스트라" in americano["options"]
    assert americano["quantity"] == 1

    latte = next(it for it in snap if it["menu"] == "핫바닐라라떼")
    assert latte["quantity"] == 1  # 2잔 추가 후 1잔 제거

    # 응답에 금지 표현 없음
    for r in responses:
        assert _no_forbidden_tokens(r), f"forbidden token in: {r}"


# ============ 시나리오 B: 디카페인 swap ============


def test_scenario_b_decaf_swap(ollama_client, model_name):
    """일반 아메리카노 → 디카페인 콜드브루로 swap (Ediya 도메인 규칙)."""
    turns = [
        "아이스 아메리카노 주세요.",
        "디카페인으로 바꿔주세요.",   # Ediya: 디카페인 = 별도 메뉴 → replace_menu
        "주문 끝낼게요.",
    ]
    cart, history, responses = _run_turns(ollama_client, model_name, turns)

    used = _tools_used(history)
    assert "add_menu" in used
    # 모델이 replace_menu 또는 add_menu(디카페인...) + remove_menu 조합 둘 다 허용
    assert ("replace_menu" in used) or (used.count("add_menu") >= 2 and "remove_menu" in used)
    assert "done" in used

    snap = cart.snapshot()
    assert len(snap) == 1
    assert "디카페인" in snap[0]["menu"]
    assert "콜드브루" in snap[0]["menu"]


# ============ 시나리오 C: 정보 질의 후 주문 ============


def test_scenario_c_inquire_then_order(ollama_client, model_name):
    """단 거 추천 → 추천받은 메뉴 직접 주문 → 완료.

    NOTE: "그럼" 같은 약한 conjunction은 E2B에서 inquiry continuation으로 분류되어
    도구 호출이 약화됨. 자연스러운 직접 주문 패턴으로 검증.
    """
    turns = [
        "단 거 추천해주세요.",
        "아이스 카페모카 한 잔 주세요.",  # "그럼" 없이 명확한 주문
        "주문 끝낼게요.",
    ]
    cart, history, responses = _run_turns(ollama_client, model_name, turns)

    used = _tools_used(history)
    assert "inquire_menu_info" in used or any(
        "카페모카" in r or "달콤" in r or "추천" in r for r in responses
    )
    assert "add_menu" in used
    assert "done" in used

    snap = cart.snapshot()
    assert len(snap) >= 1
    assert any(it["menu"] == "아이스카페모카" for it in snap)
