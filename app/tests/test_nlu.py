"""NLU 단위 테스트 — 결정론 영역 100% 검증.

eval 14건 실패 시나리오를 NLU 입력으로 던졌을 때 올바른 IntentProposal이 나오는지.
"""
from __future__ import annotations

import pytest

from app.nlu import extract_intent
from app.nlu.aliases import detect_missing_options, detect_option_aliases
from app.nlu.domain_rules import apply_decaf_rule
from app.nlu.menu_resolver import (
    detect_family_ambiguity,
    detect_temperature_ambiguity,
    resolve_exact_menu,
    resolve_loose_menu,
    resolve_menu_from_utterance,
)
from app.nlu.normalize import extract_quantity, normalize_whitespace


# ---------- normalize ----------

class TestNormalize:
    def test_collapse_spaces(self):
        assert normalize_whitespace("  아이스   아메리카노  ") == "아이스 아메리카노"

    def test_quantity_digit(self):
        assert extract_quantity("아아 2잔") == 2
        assert extract_quantity("3개 주세요") == 3

    def test_quantity_korean(self):
        assert extract_quantity("아아 두 잔") == 2
        assert extract_quantity("한 잔") == 1
        assert extract_quantity("세 잔 주세요") == 3
        assert extract_quantity("두잔") == 2
        assert extract_quantity("다섯 잔") == 5

    def test_quantity_default(self):
        assert extract_quantity("아이스 아메리카노") == 1
        assert extract_quantity("") == 1
        assert extract_quantity("주세요") == 1


# ---------- aliases (옵션) ----------

class TestOptionAliases:
    def test_simple(self):
        assert "샷추가" in detect_option_aliases("샷 추가")
        assert "투샷추가" in detect_option_aliases("투샷 추가해주세요")
        assert "트리플샷추가" in detect_option_aliases("샷 세 번 추가")

    def test_size(self):
        assert "엑스트라" in detect_option_aliases("엑스트라 사이즈로")
        assert "엑스트라" in detect_option_aliases("엑사이즈")

    def test_syrup(self):
        assert "바닐라시럽 추가" in detect_option_aliases("바닐라 시럽 추가")
        assert "헤이즐넛시럽 추가" in detect_option_aliases("헤이즐넛 시럽")

    def test_sweetness(self):
        assert "덜달게" in detect_option_aliases("덜달게 부탁드려요")

    def test_no_overlap_consumes_substring(self):
        # "투샷 추가" → 투샷추가 1개만. "샷추가"가 또 잡히면 안 됨.
        opts = detect_option_aliases("아아 투샷 추가")
        assert opts == ["투샷추가"]

    def test_missing_options(self):
        assert "두유" in detect_missing_options("두유 넣어주세요")
        assert "오트밀크" in detect_missing_options("오트밀크 라떼")
        assert detect_missing_options("샷추가") == []


# ---------- domain rules (디카페인) ----------

class TestDecafRule:
    def test_decaf_americano_maps_to_coldbrew(self):
        r = apply_decaf_rule("디카페인 아메리카노 한 잔")
        assert r is not None
        menu, note = r
        assert menu == "디카페인콜드브루아메리카노"

    def test_decaf_latte_maps_to_coldbrew_latte(self):
        r = apply_decaf_rule("디카페인 라떼")
        assert r is not None
        menu, _ = r
        assert menu == "디카페인콜드브루라떼"

    def test_decaf_hot_rejected(self):
        r = apply_decaf_rule("디카페인 핫 아메리카노")
        assert r is not None
        assert r[0] == "REJECT_HOT_DECAF"

    def test_non_decaf_passes_through(self):
        assert apply_decaf_rule("아이스 아메리카노") is None


# ---------- menu resolver ----------

class TestMenuResolver:
    def test_exact_menu_match(self):
        assert resolve_exact_menu("아이스 아메리카노 한 잔") == "아이스아메리카노"
        assert resolve_exact_menu("아이스 카페라떼 두 잔") == "아이스카페라떼"
        assert resolve_exact_menu("핫 아메리카노") == "핫아메리카노"

    def test_loose_latte_correction(self):
        # "아이스 라떼" → 카페라떼 보정
        assert resolve_loose_menu("아이스 라떼 주세요") == "아이스카페라떼"
        assert resolve_loose_menu("핫 라떼") == "핫카페라떼"

    def test_loose_skips_qualified_latte(self):
        # "헤이즐넛 라떼"는 loose 보정 대상 아님 — 다른 경로로 처리
        assert resolve_loose_menu("아이스 헤이즐넛 라떼") is None

    def test_temperature_ambiguity_detected(self):
        assert detect_temperature_ambiguity("아메리카노 한 잔") is True
        assert detect_temperature_ambiguity("라떼 주세요") is True
        assert detect_temperature_ambiguity("아이스 아메리카노") is False
        assert detect_temperature_ambiguity("핫 라떼") is False

    def test_family_ambiguity_latte(self):
        fam = detect_family_ambiguity("라떼 한 잔")
        assert fam is not None
        family, candidates = fam
        assert family == "라떼"
        assert len(candidates) >= 4  # 아이스/핫 카페/연유/헤이즐넛/바닐라

    def test_family_ambiguity_coffee(self):
        fam = detect_family_ambiguity("커피 한 잔")
        assert fam is not None
        assert fam[0] == "커피"

    def test_no_family_when_specified(self):
        assert detect_family_ambiguity("아이스 헤이즐넛 라떼") is None

    def test_resolve_full_americano(self):
        menu, cands = resolve_menu_from_utterance("아이스 아메리카노")
        assert menu == "아이스아메리카노"
        assert cands == []

    def test_resolve_slang_aa(self):
        # '아아'는 profile.yaml에 등록된 슬랭
        menu, _ = resolve_menu_from_utterance("아아")
        assert menu == "아이스아메리카노"

    def test_resolve_ambiguous_latte(self):
        menu, cands = resolve_menu_from_utterance("라떼 한 잔")
        # 가족명 — 메뉴 None, 후보 여러 개
        assert menu is None or menu in cands  # exact 매칭이 더 강하면 None X
        # 'unspecified 라떼' → 후보 ≥ 1
        assert len(cands) >= 1 or menu is not None


# ---------- extractor end-to-end ----------

class TestExtractor:
    def test_eval_a1_ice_americano(self):
        p = extract_intent("아이스 아메리카노 한 잔")
        assert p.action == "add"
        assert len(p.intents) == 1
        i = p.intents[0]
        assert i.menu_kr == "아이스아메리카노"
        assert i.quantity == 1
        assert i.options == []
        assert i.ambiguity is None

    def test_eval_a2_two_lattes(self):
        p = extract_intent("아이스 카페라떼 두 잔 주세요")
        i = p.intents[0]
        assert i.menu_kr == "아이스카페라떼"
        assert i.quantity == 2

    def test_eval_b1_slang_aa(self):
        p = extract_intent("아아 한 잔")
        i = p.intents[0]
        assert i.menu_kr == "아이스아메리카노"
        assert i.ambiguity is None

    def test_eval_c1_extra_size(self):
        # 메뉴 + 사이즈 옵션
        p = extract_intent("아이스 아메리카노 한 잔에 엑스트라 사이즈로")
        i = p.intents[0]
        assert i.menu_kr == "아이스아메리카노"
        assert "엑스트라" in i.options

    def test_eval_c2_aa_shot(self):
        p = extract_intent("아아에 샷추가")
        i = p.intents[0]
        assert i.menu_kr == "아이스아메리카노"
        assert "샷추가" in i.options

    def test_eval_c3_latte_two_shot_is_family_ambiguous(self):
        # "라떼" 단독 + 투샷추가 → 메뉴는 모호하지만 옵션은 캡처
        p = extract_intent("라떼에 투샷 추가해주세요")
        i = p.intents[0]
        assert i.ambiguity == "menu_family"
        assert "투샷추가" in i.options

    def test_eval_d1_soymilk_missing(self):
        p = extract_intent("아이스 아메리카노에 두유 넣어주세요")
        i = p.intents[0]
        assert "두유" in i.missing_options
        assert i.ambiguity == "option_missing"

    def test_eval_d2_oatmilk_latte_missing(self):
        p = extract_intent("오트밀크 라떼 한 잔")
        i = p.intents[0]
        assert "오트밀크" in i.missing_options or "오트" in i.missing_options

    def test_eval_f1_ambiguous_americano(self):
        # 온도 없는 아메리카노 → 모호
        p = extract_intent("아메리카노 한 잔")
        i = p.intents[0]
        assert i.menu_kr is None
        assert i.ambiguity == "temperature"

    def test_eval_f2_ambiguous_latte(self):
        p = extract_intent("라떼 한 잔")
        i = p.intents[0]
        assert i.menu_kr is None
        assert i.ambiguity == "menu_family"
        assert len(i.ambiguity_candidates) >= 2

    def test_eval_i1_decaf_americano(self):
        p = extract_intent("디카페인 아메리카노 한 잔")
        i = p.intents[0]
        assert i.menu_kr == "디카페인콜드브루아메리카노"
        assert any("디카페인" in n for n in i.notes)

    def test_eval_i2_decaf_hot_rejected(self):
        p = extract_intent("디카페인 핫 아메리카노")
        i = p.intents[0]
        assert i.ambiguity == "decaf_hot"
        assert i.menu_kr is None

    def test_eval_e1_two_menus_split(self):
        # "아아 하나랑 핫 카페라떼 하나" → 2 intents
        p = extract_intent("아아 하나랑 핫 카페라떼 하나")
        assert len(p.intents) == 2
        menus = sorted([i.menu_kr for i in p.intents if i.menu_kr])
        assert "아이스아메리카노" in menus
        assert "핫카페라떼" in menus

    def test_remove_action(self):
        p = extract_intent("전부 취소해주세요")
        assert p.action == "remove"

    def test_inquire_action(self):
        p = extract_intent("디카페인 있어요?")
        assert p.action == "inquire"

    def test_undo_action(self):
        p = extract_intent("방금 한 거 되돌려줘")
        assert p.action == "undo"

    def test_done_action(self):
        p = extract_intent("주문 끝낼게요")
        assert p.action == "done"


# ---------- Phase 4 회귀 fix 케이스 ----------

class TestPhase4Regressions:
    def test_change_option_promotes_to_add_when_cart_empty(self):
        # "아아에 샷추가" — 카트 비어있으면 add_menu여야 함
        p = extract_intent("아아에 샷추가", cart_menus=[])
        assert p.action == "add"
        i = p.intents[0]
        assert i.menu_kr == "아이스아메리카노"
        assert "샷추가" in i.options

    def test_change_option_stays_when_cart_has_menu(self):
        # 같은 발화 + 카트에 메뉴 있으면 change_option 유지
        p = extract_intent("아아에 샷추가", cart_menus=["아이스아메리카노"])
        assert p.action == "change_option"

    def test_extra_size_with_empty_cart_promotes(self):
        p = extract_intent(
            "아이스 아메리카노 한 잔에 엑스트라 사이즈로", cart_menus=[]
        )
        assert p.action == "add"
        i = p.intents[0]
        assert "엑스트라" in i.options

    def test_sweetness_with_empty_cart_promotes(self):
        p = extract_intent("덜달게 부탁드려요 아이스 카페라떼", cart_menus=[])
        assert p.action == "add"
        i = p.intents[0]
        assert i.menu_kr == "아이스카페라떼"
        assert "덜달게" in i.options

    def test_asyatchu_slang_does_not_pull_shot_option(self):
        # "아샷추 하나만 담아주세요" — 슬랭은 레몬아이스티, 옵션에 샷추가 X
        p = extract_intent("아샷추 하나만 담아주세요")
        i = p.intents[0]
        assert i.menu_kr == "레몬아이스티"
        assert "샷추가" not in i.options

    def test_e2_compound_split_with_e(self):
        # "아아 둘에 핫 아메리카노 하나" → 2 intents
        p = extract_intent("아아 둘에 핫 아메리카노 하나")
        assert len(p.intents) == 2
        menus = sorted([i.menu_kr for i in p.intents if i.menu_kr])
        assert "아이스아메리카노" in menus
        assert "핫아메리카노" in menus
        qtys = sorted([i.quantity for i in p.intents])
        assert qtys == [1, 2]
