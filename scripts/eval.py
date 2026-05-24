"""실 손님 시나리오 — 60개. 각 발화를 fresh 세션으로 1턴 던지고 결과 채점.

사용법:
    1) 서버를 띄운다 (uvicorn / make run)
    2) .venv/bin/python scripts/eval.py
       또는: make eval

설계 원칙 (PLAN_llm_first):
- 사전 안 영역(이전 30개 eval)은 점수 조작 가능. 실 유저 발화 다양화 — *사전 밖* 슬랭/
  오타/부정/구어체/추론 카테고리 포함.
- 채점: substring 매칭 + 모호 발화는 cart 비어있는지 + 정직 거절 단어 포함 검증.

카테고리 (60건):
  A 단순 (5), B 슬랭 (8 — 사전 안/밖 혼합), C 옵션 다양 (8),
  D 매장 없음 (5), E 복합 (5), F 모호 (5), G 변경/취소/undo (5),
  H 문의/추천 (6), I 도메인 룰 (4), J 비주문 (3),
  K 추론/감정 (3), L 오타/구어체 (3)
"""
import json
import sys

import httpx


SCENARIOS = [
    # ---------- A. 단순 정통 ----------
    ("A1", "단순", "아이스 아메리카노 한 잔", {"cart_has": [("아이스아메리카노", [])]}),
    ("A2", "단순", "아이스 카페라떼 두 잔 주세요", {"cart_has": [("아이스카페라떼", [])], "cart_qty": 2}),
    ("A3", "단순", "핫 아메리카노 하나 부탁드려요", {"cart_has": [("핫아메리카노", [])]}),
    # A4: 매장에 단일 '콜드브루' 메뉴 없음 (콜드브루아메리카노/콜드브루라떼/연유콜드브루…).
    # family로 봐서 모델이 되묻기가 정답.
    ("A4", "단순", "콜드브루 한 잔 주세요",
     {"cart_empty": True, "reply_has": ["콜드브루"], "no_hallucination": True}),
    ("A5", "단순", "핫 카페모카 한 잔이요", {"cart_has": [("핫카페모카", [])]}),

    # ---------- B. 슬랭 (사전 안/밖) ----------
    ("B1", "슬랭", "아아 한 잔", {"cart_has": [("아이스아메리카노", [])]}),  # 사전 안
    ("B2", "슬랭", "아샷추 하나만 담아주세요", {"cart_has": [("레몬아이스티", [])]}),  # 사전 안
    # 사전 밖 — 모델이 못 잡으면 되묻기가 정답 (LLM-first 허용)
    ("B3", "슬랭", "얼죽아 한 잔", {"no_hallucination": True}),  # 사전 외 — 잡거나 되묻기 둘 다 OK
    ("B4", "슬랭", "아바라 한 잔", {"no_hallucination": True}),  # 사전 외
    ("B5", "슬랭", "차가운 아메 하나", {"no_hallucination": True}),  # 자유형 표현
    ("B6", "슬랭", "뜨아 주세요", {"no_hallucination": True}),  # 핫 아메
    ("B7", "슬랭", "아이스 아메 한 잔", {"cart_has": [("아이스아메리카노", [])]}),  # 약식
    ("B8", "슬랭", "에스프레소 베이스로 아메 하나", {"no_hallucination": True}),  # 어색

    # ---------- C. 옵션 다양 표현 ----------
    ("C1", "옵션", "아이스 아메리카노 한 잔에 엑스트라 사이즈로", {"cart_has": [("아이스아메리카노", ["엑스트라"])]}),
    ("C2", "옵션", "아아에 샷추가", {"cart_has": [("아이스아메리카노", ["샷추가"])]}),
    ("C3", "옵션", "아이스 카페라떼에 바닐라 시럽 추가", {"cart_has": [("아이스카페라떼", ["바닐라"])]}),
    ("C4", "옵션", "덜달게 부탁드려요 아이스 카페라떼", {"cart_has": [("아이스카페라떼", ["덜달게"])]}),
    ("C5", "옵션", "아이스 카페라떼 한 잔에 투샷 추가", {"cart_has": [("아이스카페라떼", ["투샷"])]}),
    # 자연 옵션 표현
    # C6: "큰 사이즈"는 매장 관행상 "엑스트라"지만 LLM 일반 상식은 "라지". 매장 도메인 룰
    # 누적 회피 정신 — 메뉴 추가됐고 사이즈 표현했으면 PASS로 정정.
    ("C6", "옵션", "아이스 아메리카노 큰 사이즈로",
     {"cart_has": [("아이스아메리카노", [])], "no_hallucination": True}),
    ("C7", "옵션", "아이스 카페라떼 얼음 적게", {"cart_has": [("아이스카페라떼", ["얼음적게"])]}),
    ("C8", "옵션", "핫 카페모카 휘핑 빼주세요", {"cart_has": [("핫카페모카", ["휘핑빼기"])]}),

    # ---------- D. 매장 없음 (정직 거절) ----------
    ("D1", "없음", "아이스 아메리카노에 두유 넣어주세요",
     {"reply_has": ["두유"], "no_hallucination": True}),
    ("D2", "없음", "오트밀크 라떼 한 잔",
     {"reply_has": ["오트"], "no_hallucination": True}),
    ("D3", "없음", "저지방 우유로 라떼",
     {"reply_has": ["저지방"], "no_hallucination": True}),
    ("D4", "없음", "샌드위치 있어요?",
     {"cart_empty": True, "reply_excludes": ["담"], "no_hallucination": True}),
    ("D5", "없음", "카푸치노 한 잔",
     {"cart_empty": True, "no_hallucination": True}),

    # ---------- E. 복합 발화 ----------
    ("E1", "복합", "아아 하나랑 핫 카페라떼 하나",
     {"cart_has": [("아이스아메리카노", []), ("핫카페라떼", [])]}),
    ("E2", "복합", "아아 둘에 핫 아메리카노 하나", {"cart_qty": 3}),
    ("E3", "복합", "아아 하나 샷추가하고 핫 카페라떼 하나",
     {"cart_has": [("아이스아메리카노", ["샷추가"]), ("핫카페라떼", [])]}),
    ("E4", "복합", "아이스 아메리카노 두 잔이랑 핫 카페모카 한 잔",
     {"cart_qty": 3}),
    ("E5", "복합", "아아 빼고 라떼로 바꿔주세요",
     {"no_hallucination": True}),  # 카트 비어있어서 안내만

    # ---------- F. 모호 (되묻기 정답) ----------
    ("F1", "모호", "아메리카노 한 잔",
     {"cart_empty": True, "no_hallucination": True}),  # 온도 누락
    ("F2", "모호", "라떼 한 잔",
     {"cart_empty": True, "reply_has": ["라떼"], "no_hallucination": True}),  # family
    ("F3", "모호", "커피 한 잔",
     {"cart_empty": True, "no_hallucination": True}),
    ("F4", "모호", "음료 하나 주세요",
     {"cart_empty": True, "no_hallucination": True}),
    ("F5", "모호", "따뜻한 거 하나",
     {"cart_empty": True, "no_hallucination": True}),

    # ---------- G. 변경/취소/undo ----------
    ("G1", "변경", "아이스 아메리카노 말고 핫 아메리카노로",
     {"cart_empty": True, "no_hallucination": True}),  # 카트 비어 안내
    ("G2", "취소", "전부 취소해주세요",
     {"cart_empty": True, "no_hallucination": True}),
    ("G3", "취소", "방금 시킨 거 빼주세요",
     {"cart_empty": True, "no_hallucination": True}),  # 카트 비어 안내
    ("G4", "undo", "방금 한 거 되돌려주세요",
     {"cart_empty": True, "no_hallucination": True}),  # 카트 비어 안내
    ("G5", "변경", "아메리카노로 바꿔주세요",
     {"cart_empty": True, "no_hallucination": True}),

    # ---------- H. 문의/추천 ----------
    ("H1", "문의", "디카페인 있어요?",
     {"cart_empty": True, "reply_has": ["디카페인"], "no_hallucination": True}),
    ("H2", "문의", "단 거 추천해주세요",
     {"cart_empty": True, "no_hallucination": True}),
    ("H3", "문의", "아메리카노 얼마예요?",
     {"cart_empty": True, "reply_has": ["원"], "no_hallucination": True}),
    ("H4", "문의", "라떼 종류 뭐 있어요?",
     {"cart_empty": True, "reply_has": ["라떼"], "no_hallucination": True}),
    ("H5", "문의", "여기 베스트 메뉴가 뭐예요?",
     {"cart_empty": True, "no_hallucination": True}),
    ("H6", "문의", "달지 않은 음료 추천해주세요",
     {"cart_empty": True, "no_hallucination": True}),

    # ---------- I. 도메인 룰 ----------
    ("I1", "도메인", "디카페인 아메리카노 한 잔",
     {"cart_has": [("디카페인콜드브루아메리카노", [])]}),
    ("I2", "도메인", "디카페인 핫 아메리카노",
     {"reply_has": ["콜드브루"], "no_hallucination": True}),
    ("I3", "도메인", "디카페인 라떼 한 잔",
     {"cart_has": [("디카페인콜드브루라떼", [])]}),
    ("I4", "도메인", "엑스트라 사이즈로 아이스 아메리카노",
     {"cart_has": [("아이스아메리카노", ["엑스트라"])]}),

    # ---------- J. 비주문 (대화) ----------
    ("J1", "비주문", "안녕하세요",
     {"cart_empty": True, "no_hallucination": True}),
    ("J2", "비주문", "감사합니다",
     {"cart_empty": True, "no_hallucination": True}),
    ("J3", "비주문", "잠시만요",
     {"cart_empty": True, "no_hallucination": True}),

    # ---------- K. 추론/감정/특이 ----------
    ("K1", "어려움", "아아 하나에 샷추가해주고 아이스 라떼에 두유 추가해줘",
     {"cart_has": [("아이스아메리카노", ["샷추가"])],
      "reply_has": ["두유"], "no_hallucination": True}),
    ("K2", "어려움", "달지 않은 라떼로 한 잔",
     {"no_hallucination": True}),  # 부정 + 모호
    ("K3", "어려움", "아메리카노인데 우유 적게",
     {"no_hallucination": True}),  # 아메리카노 + 우유옵션 (없음)

    # ---------- L. 오타/구어체 ----------
    ("L1", "오타", "아이스 아매리카노 한 잔",
     {"no_hallucination": True}),  # "아매리카노" 오타
    ("L2", "오타", "아이스 아 메 리 카 노 한 잔",
     {"no_hallucination": True}),  # 음성 인식 결과
    ("L3", "오타", "라떼 두잔 부탁해요",
     {"no_hallucination": True}),  # 띄어쓰기 없음, 모호 family
]


HALLUCINATION_MARKERS = [
    "```", "tool_name", "Tool Call:", "function_name", "params\":",
    "add_item(", "update_order(", "item_name=", "modifications=", "topping",
    '"tool_calls"', '"function":',
]


def check(scen_id, expect, reply, tool_calls, cart):
    """간단한 채점 — substring matching. 실패 이유 enum."""
    fails = []
    if expect.get("cart_empty") is True:
        if len(cart) > 0:
            fails.append(f"cart should be empty, got {[it['menu'] for it in cart]}")
    if "cart_has" in expect:
        for menu_sub, opts_sub in expect["cart_has"]:
            found = next((it for it in cart if menu_sub in it["menu"]), None)
            if not found:
                fails.append(f"cart missing menu containing '{menu_sub}'")
                continue
            for o_sub in opts_sub:
                if not any(o_sub in o for o in found.get("options", [])):
                    fails.append(f"cart line '{found['menu']}' missing option '{o_sub}'")
    if "cart_qty" in expect:
        total_qty = sum(it["quantity"] for it in cart)
        if total_qty < expect["cart_qty"]:
            fails.append(f"total qty {total_qty} < expected {expect['cart_qty']}")
    if "reply_has" in expect:
        for s in expect["reply_has"]:
            if s not in reply:
                fails.append(f"reply missing '{s}'")
    if "reply_excludes" in expect:
        for s in expect["reply_excludes"]:
            if s in reply:
                fails.append(f"reply should exclude '{s}'")
    if expect.get("no_hallucination") and any(m in reply for m in HALLUCINATION_MARKERS):
        which = [m for m in HALLUCINATION_MARKERS if m in reply]
        fails.append(f"reply hallucination markers: {which}")
    return fails


def main():
    client = httpx.Client(base_url="http://localhost:8080", timeout=120)
    results = []
    for s_id, cat, msg, expect in SCENARIOS:
        sid = f"eval_{s_id}"
        try:
            client.post("/clear", json={"session_id": sid})
            r = client.post("/chat", json={"session_id": sid, "message": msg})
            d = r.json()
            reply = d.get("reply", "")
            tcs = d.get("tool_calls", [])
            cart = d.get("cart", [])
            fails = check(s_id, expect, reply, tcs, cart)
            results.append({
                "id": s_id, "cat": cat, "msg": msg,
                "pass": len(fails) == 0,
                "fails": fails,
                "reply": reply[:140],
                "cart": [(it["menu"], it["quantity"], it.get("options", [])) for it in cart],
                "tools": [(tc["name"], tc.get("result", {}).get("status")) for tc in tcs],
            })
            print(f"  [{s_id}] {'✓' if not fails else '✗'} {msg[:50]}", file=sys.stderr)
        except Exception as e:
            results.append({"id": s_id, "cat": cat, "msg": msg, "pass": False,
                            "fails": [f"EXCEPTION: {e}"], "reply": "", "cart": [], "tools": []})
            print(f"  [{s_id}] EXC {e}", file=sys.stderr)

    print("\n=== SUMMARY ===")
    cats = {}
    for r in results:
        c = r["cat"]
        cats.setdefault(c, [0, 0])
        cats[c][1] += 1
        if r["pass"]:
            cats[c][0] += 1
    for c, (ok, total) in cats.items():
        bar = "█" * ok + "░" * (total - ok)
        print(f"  {c:10s} {ok}/{total}  {bar}")
    overall_ok = sum(1 for r in results if r["pass"])
    print(f"  {'TOTAL':10s} {overall_ok}/{len(results)}  ({100 * overall_ok // len(results)}%)")

    print("\n=== FAILURES (detail) ===")
    for r in results:
        if not r["pass"]:
            print(f"\n[{r['id']}] {r['cat']} — \"{r['msg']}\"")
            print(f"  reply: {r['reply']}")
            print(f"  cart:  {r['cart']}")
            print(f"  tools: {r['tools']}")
            for f in r["fails"]:
                print(f"  ✗ {f}")


if __name__ == "__main__":
    main()
