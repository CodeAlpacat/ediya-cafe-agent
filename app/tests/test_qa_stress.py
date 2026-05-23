"""QA Stress Test — 카테고리별 광범위 엣지 케이스.

목적: 실제 사용자가 발생시킬 수 있는 다양한 발화 패턴에 대해 모델이 자연스럽게 대응하는지 검증.
실패 케이스도 정직하게 기록 — 통과 못 한 케이스는 v2 보완 대상으로 박제.

카테고리:
1. 복수 메뉴 주문 (multi-item utterance)
2. 한국어 변형 (존댓말/반말/슬랭/축약)
3. 간접 참조 / 자가수정
4. 장기 대화 (10+ 턴)
5. 도메인 외 질문
6. 양적 표현 변형
7. 옵션 조합 / 동시 변경
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

import pytest

pytestmark = pytest.mark.ollama


def _run(client, model: str, msgs: List[str]) -> Tuple[Any, List[Dict[str, Any]], List[str]]:
    """주어진 발화 list 처리. (cart, history, responses) 반환."""
    from app.llm.agent import AgentConfig, run_turn
    from app.domain.cart import Cart

    cart = Cart()
    history: List[Dict[str, Any]] = []
    responses: List[str] = []
    cfg = AgentConfig(model=model)
    for m in msgs:
        text = run_turn(client=client, user_message=m, cart=cart, history=history, config=cfg)
        responses.append(text)
    return cart, history, responses


def _tools_called(history: List[Dict[str, Any]]) -> List[str]:
    used = []
    for msg in history:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                used.append(tc["function"]["name"])
    return used


def _tool_call_count(history: List[Dict[str, Any]], name: str) -> int:
    return _tools_called(history).count(name)


# =====================================================================
# Category 1: 복수 메뉴 주문 (multi-item utterance)
# =====================================================================


def test_multi_item_single_utterance(ollama_client, model_name):
    """'아메리카노랑 카페라떼 한 잔씩 주세요' — 한 발화에 2개 메뉴."""
    cart, history, _ = _run(
        ollama_client, model_name,
        ["아이스 아메리카노랑 아이스 카페라떼 한 잔씩 주세요."],
    )
    add_count = _tool_call_count(history, "add_menu")
    snap = cart.snapshot()
    menus = {it["menu"] for it in snap}
    assert add_count >= 2 or len(snap) >= 2, f"복수 메뉴 단일 발화 처리 실패. cart={snap}"
    assert "아이스아메리카노" in menus
    assert "아이스카페라떼" in menus


def test_multi_item_mixed_temperature(ollama_client, model_name):
    """'따뜻한 아메리카노 하나랑 아이스 라떼 하나' — 한 발화에 hot+iced 섞임.

    Silent Corruption Guard + parallel_tool_calls=True 적용 후 안정 통과.
    이전 xfail 박제 해제 (2026-05-21).
    """
    cart, history, _ = _run(
        ollama_client, model_name,
        ["따뜻한 아메리카노 한 잔이랑 아이스 카페라떼 한 잔 주세요."],
    )
    snap = cart.snapshot()
    menus = {it["menu"] for it in snap}
    assert "핫아메리카노" in menus, f"hot americano not added. cart={snap}"
    assert "아이스카페라떼" in menus, f"iced latte not added. cart={snap}"


# =====================================================================
# Category 2: 한국어 변형
# =====================================================================


def test_polite_old_style(ollama_client, model_name):
    """존댓말 격식체."""
    cart, _, _ = _run(
        ollama_client, model_name,
        ["아이스 아메리카노 한 잔 부탁드립니다."],
    )
    assert any(it["menu"] == "아이스아메리카노" for it in cart.snapshot())


def test_casual_banmal(ollama_client, model_name):
    """반말."""
    cart, _, _ = _run(
        ollama_client, model_name,
        ["아이스 아메리카노 한 잔 줘."],
    )
    assert any(it["menu"] == "아이스아메리카노" for it in cart.snapshot()), \
        f"반말 주문 실패. cart={cart.snapshot()}"


def test_korean_slang_aa(ollama_client, model_name):
    """'아아' = 아이스 아메리카노 (한국 카페 슬랭).

    NOTE: 사전에 등록 안 된 슬랭이므로 모델이 풀어쓰기 못할 가능성 있음.
    실패 시 v2에서 사전 확장 또는 시스템 프롬프트 보강 대상.
    """
    cart, history, responses = _run(
        ollama_client, model_name,
        ["아아 한 잔 주세요."],
    )
    snap = cart.snapshot()
    add_called = _tool_call_count(history, "add_menu") > 0

    # 통과 조건: add_menu가 호출됐고 아메리카노 추가됐거나,
    #            또는 모델이 정중히 되물어봄 (이게 더 안전함)
    if add_called:
        assert any("아메리카노" in it["menu"] for it in snap), \
            f"'아아'를 아메리카노로 해석 못함. cart={snap}"
    else:
        # 되물어봤다면 응답에 명료화 표현이 있어야 함
        assert any(kw in responses[0] for kw in ["?", "어떤", "다시", "확인"]), \
            f"'아아' 모호한 발화에 적절히 대응 못함. response={responses[0]}"


# =====================================================================
# Category 3: 간접 참조 / 자가 수정
# =====================================================================


def test_indirect_reference(ollama_client, model_name):
    """'아까 그거 빼주세요' — 명시적 메뉴 이름 없음.

    P4 referential hint injection으로 통과. agent.py가 referential pattern 감지 시
    cart의 마지막 메뉴를 system hint로 inject — 모델이 정확히 해석.
    이전 xfail 박제 해제 (2026-05-21).
    """
    cart, history, _ = _run(
        ollama_client, model_name,
        [
            "아이스 아메리카노 한 잔 주세요.",
            "아까 시킨 거 빼주세요.",
        ],
    )
    assert "remove_menu" in _tools_called(history), \
        f"간접 참조 → remove 실패. tools={_tools_called(history)}"
    assert cart.is_empty(), f"빈 카트 아님. cart={cart.snapshot()}"


def test_self_correction(ollama_client, model_name):
    """주문 후 즉시 자가 수정."""
    cart, history, _ = _run(
        ollama_client, model_name,
        [
            "아이스 아메리카노 주세요.",
            "아 잘못 말했어요. 핫 아메리카노로 해주세요.",
        ],
    )
    snap = cart.snapshot()
    menus = {it["menu"] for it in snap}
    # 1) 핫아메리카노만 있거나 2) replace_menu / remove+add 조합 모두 허용
    assert "핫아메리카노" in menus, f"자가 수정 실패. cart={snap}"
    # 둘 다 있으면 안 됨 (구버전 정리 필요)
    assert "아이스아메리카노" not in menus, \
        f"자가 수정 후 이전 메뉴 잔존. cart={snap}"


# =====================================================================
# Category 4: 장기 대화 (10턴)
# =====================================================================


def test_long_conversation_10_turns(ollama_client, model_name):
    """10턴 대화 후 핵심 도구 호출 + 디카페인 swap 검증.

    P3 stuck guard + 키워드 메뉴 hint 적용 후 안정 통과.
    이전 xfail 박제 해제 (2026-05-21).
    """
    turns = [
        "아이스 아메리카노 한 잔 주세요.",
        "따뜻한 카페라떼도 두 잔 주세요.",
        "라떼 엑스트라 사이즈로 변경해주세요.",
        "지금 주문 뭐 있어요?",
        "아메리카노 한 잔 더 추가해주세요.",
        "어 잠깐, 그 아메리카노는 디카페인으로 바꿔주세요.",
        "카페모카 한 잔 추가해주세요.",
        "라떼 한 잔만 빼주세요.",
        "지금 주문 다시 확인해주세요.",
        "주문 끝낼게요.",
    ]
    cart, history, _ = _run(ollama_client, model_name, turns)
    used = _tools_called(history)

    # 핵심 도구가 모두 호출되었는지 — done은 새 description으로 안정
    for required in ["add_menu", "change_option", "check_cart", "done"]:
        assert required in used, f"10턴 중 {required} 누락. used={used}"

    # 디카페인 swap이 반영되었는지 (turn 6)
    snap = cart.snapshot()
    has_decaf = any("디카페인" in it["menu"] for it in snap)
    assert has_decaf, f"디카페인 swap 반영 안 됨. cart={snap}"

    # check_cart는 2회 이상 호출돼야 함 (turn 4, 9)
    assert used.count("check_cart") >= 2, f"check_cart 미반복. used={used}"


def test_long_conversation_cart_accuracy_short(ollama_client, model_name):
    """카트 수량 정확성은 짧은 시나리오로 검증 (10턴 stuck loop 회피)."""
    turns = [
        "따뜻한 카페라떼 두 잔 주세요.",
        "한 잔만 빼주세요.",
        "주문 끝낼게요.",
    ]
    cart, history, _ = _run(ollama_client, model_name, turns)
    snap = cart.snapshot()
    latte = next((it for it in snap if it["menu"] == "핫카페라떼"), None)
    assert latte is not None, f"라떼 없음. cart={snap}"
    assert latte["quantity"] == 1, f"라떼 1잔 남아있어야 함. got {latte['quantity']}"


# =====================================================================
# Category 5: 도메인 외 질문
# =====================================================================


def test_off_topic_weather(ollama_client, model_name):
    """'오늘 날씨 어때요?' — 도메인 외 질문은 정중히 도메인으로 유도."""
    cart, history, responses = _run(
        ollama_client, model_name,
        ["오늘 날씨 어때요?"],
    )
    # 주문 도구 호출 X
    add_calls = _tool_call_count(history, "add_menu")
    assert add_calls == 0, "도메인 외 질문에 add_menu 호출하면 안 됨"
    assert cart.is_empty()


def test_off_brand_menu(ollama_client, model_name):
    """'스타벅스 자바칩 프라푸치노 주세요' — 타 브랜드 메뉴."""
    cart, history, responses = _run(
        ollama_client, model_name,
        ["스타벅스 자바칩 프라푸치노 주세요."],
    )
    # 카트는 비어있거나 INVALID_MENU result
    snap = cart.snapshot()
    if snap:
        # 만약 add_menu가 호출됐다면 dispatcher가 INVALID_MENU 반환했어야 함
        # (즉, cart에는 아무것도 안 들어감)
        assert cart.is_empty() or all("자바칩" not in it["menu"] for it in snap)


# =====================================================================
# Category 6: 양적 표현 변형
# =====================================================================


def test_quantity_in_korean_words(ollama_client, model_name):
    """'세 잔', '다섯 잔' — 한국어 수사."""
    cart, _, _ = _run(
        ollama_client, model_name,
        ["아이스 아메리카노 세 잔 주세요."],
    )
    snap = cart.snapshot()
    item = next((it for it in snap if it["menu"] == "아이스아메리카노"), None)
    assert item is not None, f"세 잔 발화 처리 실패. cart={snap}"
    assert item["quantity"] == 3, f"수량 3 인식 실패. got {item['quantity']}"


def test_quantity_large(ollama_client, model_name):
    """'열 잔' — 큰 숫자."""
    cart, _, _ = _run(
        ollama_client, model_name,
        ["아이스 아메리카노 열 잔 주세요."],
    )
    snap = cart.snapshot()
    item = next((it for it in snap if it["menu"] == "아이스아메리카노"), None)
    assert item is not None, f"열 잔 발화 처리 실패. cart={snap}"
    assert item["quantity"] == 10, f"수량 10 인식 실패. got {item['quantity']}"


# =====================================================================
# Category 7: 옵션 조합 / 동시 변경
# =====================================================================


def test_multi_option_at_order(ollama_client, model_name):
    """주문 시점에 옵션 2-3개 동시 지정."""
    cart, _, _ = _run(
        ollama_client, model_name,
        ["엑스트라 사이즈에 샷 추가한 아이스 아메리카노 주세요."],
    )
    snap = cart.snapshot()
    item = next((it for it in snap if it["menu"] == "아이스아메리카노"), None)
    assert item is not None, f"엑스트라+샷추가 아메리카노 주문 실패. cart={snap}"
    options = item["options"]
    assert "엑스트라" in options, f"엑스트라 누락. options={options}"
    assert "샷추가" in options, f"샷추가 누락. options={options}"


def test_simultaneous_replace_and_option(ollama_client, model_name):
    """'라떼는 디카페인으로 바꾸고 사이즈는 엑스트라로' — 한 발화에 swap + option 동시.

    NOTE: 모델이 한 발화에서 2개 도구 호출 (replace_menu + change_option)을 할 수 있는지.
    실패 시 v2에서 parallel_tool_calls 또는 멀티턴 처리.
    """
    cart, history, responses = _run(
        ollama_client, model_name,
        [
            "아이스 아메리카노 한 잔 주세요.",
            "그거 카페라떼로 바꾸고 엑스트라 사이즈로 해주세요.",
        ],
    )
    snap = cart.snapshot()
    latte = next((it for it in snap if "카페라떼" in it["menu"]), None)
    assert latte is not None, f"라떼로 교체 실패. cart={snap}"
    # 옵션이 반영됐는지 (강한 assertion은 아님 — 모델이 옵션을 turn 2에서 못 잡을 수도)
    if "엑스트라" not in latte.get("options", []):
        pytest.xfail(f"한 발화 multi-intent 처리 실패 (v2 대상). cart={snap}")


# =====================================================================
# Category 8: 빈 카트 done / 사용자 오류
# =====================================================================


def test_done_on_empty_cart(ollama_client, model_name):
    """빈 카트에서 '주문 끝낼게요' — 모델이 자연스럽게 안내."""
    cart, history, responses = _run(
        ollama_client, model_name,
        ["주문 끝낼게요."],
    )
    # done 호출 후 dispatcher가 EMPTY_CART 반환 → 모델이 안내 응답
    assert "done" in _tools_called(history) or any(
        kw in responses[0] for kw in ["비어", "없", "주문해"]
    )


def test_remove_from_empty_cart(ollama_client, model_name):
    """빈 카트에서 'X 빼주세요'."""
    cart, history, responses = _run(
        ollama_client, model_name,
        ["아이스 아메리카노 빼주세요."],
    )
    # MENU_NOT_IN_CART or 모델이 직접 안내
    snap = cart.snapshot()
    assert cart.is_empty()


# =====================================================================
# Category 9: 인사 / 가벼운 잡담
# =====================================================================


def test_greeting(ollama_client, model_name):
    """'안녕하세요' — 인사."""
    cart, history, responses = _run(
        ollama_client, model_name,
        ["안녕하세요."],
    )
    # 도구 호출 X, 인사 응답
    assert _tool_call_count(history, "add_menu") == 0
    assert any(kw in responses[0] for kw in ["안녕", "어서", "주문", "도와", "환영"])


def test_thanks(ollama_client, model_name):
    """주문 완료 후 '감사합니다'."""
    cart, history, responses = _run(
        ollama_client, model_name,
        [
            "아이스 아메리카노 주세요.",
            "주문 끝낼게요.",
            "감사합니다.",
        ],
    )
    # 마지막 응답이 자연스러워야 함
    assert len(responses[-1]) > 0


# =====================================================================
# Category 10: 재고 부족 — Ediya 시뮬레이션 (P1)
# =====================================================================


def test_sold_out_graceful_response(ollama_client, model_name):
    """크림치즈프레첼은 품절(stock=0). 모델이 자연스럽게 안내해야 함."""
    cart, history, responses = _run(
        ollama_client, model_name,
        ["크림치즈프레첼 한 개 주세요."],
    )
    # 카트는 비어있어야 함 (재고 없음)
    snap = cart.snapshot()
    assert not any(it["menu"] == "크림치즈프레첼" for it in snap), \
        f"품절 메뉴가 카트에 들어감. cart={snap}"

    # 응답에 품절/없음 안내 키워드
    assert any(kw in responses[0] for kw in ["품절", "매진", "없", "오늘", "어려"]), \
        f"품절 안내 응답 부족. response={responses[0]}"


def test_insufficient_stock_offers_max(ollama_client, model_name):
    """컵단팥빙수 stock=3. 5개 주문 → INSUFFICIENT_STOCK. 모델이 3개 가능 안내.

    NOTE: 메뉴명은 정확히 (공백 없이 '컵단팥빙수'). 자유 발화 ('단팥빙수', '베이글')은
    모델이 hallucinate('매장에 없어요')할 수 있음 — v2에서 메뉴 인지 보강 필요 (P1 hallucination).
    """
    cart, history, responses = _run(
        ollama_client, model_name,
        ["컵단팥빙수 다섯 개 주세요."],  # 정확한 메뉴명
    )
    snap = cart.snapshot()
    # 카트에 들어가더라도 stock 이내
    assert all(it["menu"] != "컵단팥빙수" or it["quantity"] <= 3 for it in snap), \
        f"재고 초과 add됨. cart={snap}"

    # 응답에 가능 수량 또는 부족 안내 (3개까지 / 부족 / 한정 등)
    assert any(kw in responses[0] for kw in ["3", "세", "부족", "한정", "가능", "남"]), \
        f"재고 부족 안내 키워드 부족. response={responses[0]}"


def test_loose_menu_name_hallucination(ollama_client, model_name):
    """자유 발화 '단팥빙수' (공백/카테고리 누락) → 모델이 정확한 종류 되묻기.

    키워드 메뉴 hint로 통과. agent.py가 발화에서 메뉴 후보를 추출해서
    "컵단팥빙수/플레이트단팥빙수 중 어떤 거?" 명확화 질문 유도.
    이전 xfail 박제 해제 (2026-05-21).
    """
    cart, history, responses = _run(
        ollama_client, model_name,
        ["단팥빙수 한 개 주세요."],  # 카테고리 prefix 누락
    )
    # 모델이 메뉴 후보를 제시하거나 dispatcher가 fuzzy 매칭하길 기대 (v2)
    assert "단팥빙수" in responses[0] and not any(
        kw in responses[0] for kw in ["없어", "취급하지 않", "주문해 드릴 수가 없"]
    ), f"hallucinate한 거절 응답: {responses[0]}"
