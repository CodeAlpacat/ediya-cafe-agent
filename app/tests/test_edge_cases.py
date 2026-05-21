"""Phase 4 RED: 엣지 케이스 — 도메인 규칙 준수 검증.

각 케이스마다 검증하는 것:
- E1 매장에 없는 메뉴: dispatcher가 INVALID_MENU 반환 + 모델이 거절 응답
- E2 모호한 메뉴: 모델이 도구 호출 없이 되묻기
- E3 온도 누락: 모델이 도구 호출 없이 "Hot이요, Ice요?" 류 질문
- E4 존재하지 않는 옵션: dispatcher가 INVALID_OPTION 반환 + 모델이 대안 안내

LLM 비결정성 고려: assertion은 키워드 포함 여부로 유연하게.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

pytestmark = pytest.mark.ollama


def _run_one_turn(client, model: str, user_msg: str):
    from app.agent import AgentConfig, run_turn
    from app.cart import Cart

    cart = Cart()
    history: List[Dict[str, Any]] = []
    text = run_turn(client=client, user_message=user_msg, cart=cart, history=history,
                    config=AgentConfig(model=model))
    return cart, history, text


def _tool_results(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """history의 tool role 메시지에서 JSON 파싱한 결과 list 반환."""
    out = []
    for msg in history:
        if msg.get("role") == "tool":
            try:
                out.append(json.loads(msg.get("content", "{}")))
            except json.JSONDecodeError:
                pass
    return out


def _tools_called(history: List[Dict[str, Any]]) -> List[str]:
    used = []
    for msg in history:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                used.append(tc["function"]["name"])
    return used


# ============ E1: 매장에 없는 메뉴 ============


def test_e1_invalid_menu_rejected(ollama_client, model_name):
    """'냉면 주세요' → dispatcher가 INVALID_MENU 반환하면 모델이 거절 + 대안 제안."""
    cart, history, text = _run_one_turn(ollama_client, model_name, "냉면 한 그릇 주세요.")

    # dispatcher가 INVALID_MENU를 반환했거나, 모델이 도구 호출 없이 거절했어야 함
    results = _tool_results(history)
    has_invalid = any(r.get("status") == "INVALID_MENU" for r in results)
    no_tool_called = len(_tools_called(history)) == 0

    assert has_invalid or no_tool_called, "모델이 냉면을 add_menu로 호출하고 dispatcher가 reject 해야 함"

    # 카트는 비어있어야 함
    assert cart.is_empty()

    # 응답에 거절/안내 표현
    assert any(kw in text for kw in ["냉면", "없", "팔지", "취급", "어려"]), f"got: {text}"


# ============ E2: 모호한 메뉴 ============


def test_e2_ambiguous_menu_asks_back(ollama_client, model_name):
    """'커피 한 잔 주세요' → 모델이 도구 호출 없이 어떤 커피인지 되물어야 함."""
    cart, history, text = _run_one_turn(ollama_client, model_name, "커피 한 잔 주세요.")

    used = _tools_called(history)
    # add_menu 호출하지 않아야 함 (또는 inquire_menu_info는 허용)
    assert "add_menu" not in used, "모호한 발화를 add_menu로 호출하면 안 됨"
    assert cart.is_empty()

    # 응답에 되묻는 표현
    assert any(kw in text for kw in ["어떤", "?", "추천", "아메리카노", "라떼", "원하"]), f"got: {text}"


# ============ E3: 온도 누락 ============


def test_e3_temperature_missing_asks_back(ollama_client, model_name):
    """'아메리카노 한 잔 주세요' (온도 미정) → 모델이 Hot/Ice 되묻기.

    NOTE: 모델이 임의로 '아이스아메리카노' 추측해서 add_menu 호출할 수도 있음.
    그런 경우엔 fail로 간주. 시스템 프롬프트 가이드 따라 되물어야 함.
    """
    cart, history, text = _run_one_turn(ollama_client, model_name, "아메리카노 한 잔 주세요.")

    used = _tools_called(history)
    # add_menu를 호출하지 않거나, 호출하더라도 명시적으로 사용자가 정한 메뉴여야 함
    # (모델이 임의 추측한 경우는 fail)

    text_lower = text.lower()
    asks_temperature = any(
        kw in text_lower for kw in ["hot", "ice", "아이스", "핫", "따뜻", "차가운", "온도"]
    )

    if "add_menu" in used:
        # 만약 add_menu 호출했다면 fail
        pytest.fail(f"온도 미정인데 add_menu 호출함. response: {text}")
    else:
        # add_menu 호출 안 했다면 되묻는 응답이어야 함
        assert asks_temperature, f"되묻기 응답에 온도 키워드 없음: {text}"


# ============ E4: 존재하지 않는 옵션 ============


def test_e4_invalid_option_rejected(ollama_client, model_name):
    """'아이스 아메리카노에 초콜릿 시럽 추가' → INVALID_OPTION 또는 모델이 대안 안내."""
    cart, history, text = _run_one_turn(
        ollama_client, model_name, "아이스 아메리카노에 초콜릿 시럽 추가해주세요."
    )

    results = _tool_results(history)

    # 둘 중 하나여야 함:
    # (a) add_menu가 호출됐고 dispatcher가 INVALID_OPTION 반환
    # (b) 모델이 도구 호출 없이 "초콜릿 시럽 없어요" 같은 응답
    has_invalid_opt = any(r.get("status") == "INVALID_OPTION" for r in results)
    has_alternative_in_text = any(kw in text for kw in ["초콜릿", "없", "다른", "안 돼", "어려"])

    assert has_invalid_opt or has_alternative_in_text, (
        f"INVALID_OPTION 반환되거나 텍스트로 안내해야 함. results={results}, text={text}"
    )

    # 카트가 비어있거나 아메리카노만 (옵션은 제외) 추가됐어야 함
    snap = cart.snapshot()
    for item in snap:
        assert "초콜릿시럽" not in item["options"]
        assert "초콜릿 시럽 추가" not in item["options"]
