"""실 손님 시나리오 — 30개. 각 발화를 fresh 세션으로 1턴 던지고 결과 채점.

사용법:
    1) 서버를 띄운다 (uvicorn / make run)
    2) .venv/bin/python scripts/eval.py
       또는: make eval

카테고리: 단순 / 슬랭 / 옵션 / 없는옵션 / 복합 / 모호 / 변경 / 취소 /
         문의 / 도메인(디카페인) / 비주문 / 어려움.
채점은 substring 매칭이라 다소 strict — 일부 false negative 있음(체크 함수 보완 가능).
baseline(2026-05-24): 22/30 ≈ 73%.
"""
import json
import httpx
import sys

SCENARIOS = [
    # (id, category, message, expect dict)
    # expect: cart_has=[("menu_substr","options_substr_list")], cart_empty=True, reply_has=[...], reply_excludes=[...], tool_includes=[...], no_hallucination=True
    # ---- A. 단순 ----
    ("A1", "단순", "아이스 아메리카노 한 잔", {"cart_has": [("아이스아메리카노", [])]}),
    ("A2", "단순", "아이스 카페라떼 두 잔 주세요", {"cart_has": [("아이스카페라떼", [])], "cart_qty": 2}),
    ("A3", "단순", "핫 아메리카노 하나 부탁드려요", {"cart_has": [("핫아메리카노", [])]}),
    # ---- B. 슬랭 ----
    ("B1", "슬랭", "아아 한 잔", {"cart_has": [("아이스아메리카노", [])]}),
    ("B2", "슬랭", "아샷추 하나만 담아주세요", {"cart_has": [("레몬아이스티", [])]}),
    # ---- C. 옵션 (단순) ----
    ("C1", "옵션", "아이스 아메리카노 한 잔에 엑스트라 사이즈로", {"cart_has": [("아이스아메리카노", ["엑스트라"])]}),
    ("C2", "옵션", "아아에 샷추가", {"cart_has": [("아이스아메리카노", ["샷추가"])]}),
    # C3: '라떼' 단독은 종류 모호(menu_family). NLU가 모호로 잡고 모델은 종류 되묻기.
    # cart_has 기대는 잘못된 채점 — 빈 카트 + 라떼 종류 되묻는 게 정답.
    ("C3", "옵션", "라떼에 투샷 추가해주세요",
     {"cart_empty": True, "reply_has": ["라떼"], "no_hallucination": True}),
    ("C4", "옵션", "아이스 카페라떼에 바닐라 시럽 추가", {"cart_has": [("아이스카페라떼", ["바닐라"])]}),
    ("C5", "옵션", "덜달게 부탁드려요 아이스 카페라떼", {"cart_has": [("아이스카페라떼", ["덜달게"])]}),
    # ---- D. 존재 안하는 옵션 (정직 거절 기대) ----
    ("D1", "없는옵션", "아이스 아메리카노에 두유 넣어주세요", {"reply_has": ["두유", "없"], "no_hallucination": True}),
    ("D2", "없는옵션", "오트밀크 라떼 한 잔", {"reply_has": ["오트", "없"], "no_hallucination": True}),
    # ---- E. 복합 발화 ----
    ("E1", "복합", "아아 하나랑 핫 카페라떼 하나", {"cart_has": [("아이스아메리카노", []), ("핫카페라떼", [])]}),
    ("E2", "복합", "아아 둘에 핫 아메리카노 하나", {"cart_qty": 3}),
    ("E3", "복합", "아아 하나 샷추가하고 핫 카페라떼 하나", {"cart_has": [("아이스아메리카노", ["샷추가"]), ("핫카페라떼", [])]}),
    # ---- F. 모호 (되묻기 기대) ----
    ("F1", "모호", "아메리카노 한 잔", {"cart_empty": True, "reply_has": ["아이스", "핫"], "no_hallucination": True}),
    ("F2", "모호", "라떼 한 잔", {"cart_empty": True, "reply_has": ["어떤", "라떼"], "no_hallucination": True}),
    ("F3", "모호", "커피 한 잔", {"cart_empty": True, "reply_has": ["어떤"], "no_hallucination": True}),
    # ---- G. 변경/취소/undo ----
    ("G1", "변경", "아이스 아메리카노 말고 핫 아메리카노로", {"cart_empty": True, "reply_has": [], "no_hallucination": True}),  # 카트 비어있으니 안내
    ("G2", "취소", "전부 취소해주세요", {"cart_empty": True, "no_hallucination": True}),
    # ---- H. 문의 ----
    ("H1", "문의", "디카페인 있어요?", {"cart_empty": True, "reply_has": ["디카페인"], "no_hallucination": True}),
    ("H2", "문의", "단 거 추천해주세요", {"cart_empty": True, "no_hallucination": True}),
    ("H3", "문의", "아메리카노 얼마예요?", {"cart_empty": True, "reply_has": ["원"], "no_hallucination": True}),
    ("H4", "문의", "라떼 종류 뭐 있어요?", {"cart_empty": True, "reply_has": ["라떼"], "no_hallucination": True}),
    ("H5", "문의", "샌드위치 있어요?", {"cart_empty": True, "reply_excludes": ["담"], "no_hallucination": True}),
    # ---- I. 도메인 룰 ----
    ("I1", "도메인", "디카페인 아메리카노 한 잔", {"cart_has": [("디카페인콜드브루아메리카노", [])]}),
    ("I2", "도메인", "디카페인 핫 아메리카노", {"reply_has": ["콜드브루"], "no_hallucination": True}),  # 안내
    # ---- J. 비주문 ----
    ("J1", "비주문", "안녕하세요", {"cart_empty": True, "no_hallucination": True}),
    ("J2", "비주문", "감사합니다", {"cart_empty": True, "no_hallucination": True}),
    # ---- K. 매우 어려운 케이스 ----
    ("K1", "어려움", "아아 하나에 샷추가해주고 아이스 라떼에 두유 추가해줘", {
        "cart_has": [("아이스아메리카노", ["샷추가"])],   # 아아는 처리되어야
        "reply_has": ["두유", "없"],                       # 두유는 거절
        "no_hallucination": True,
    }),
]

HALLUCINATION_MARKERS = [
    "```", "tool_name", "Tool Call:", "function_name", "params\":",
    "add_item(", "update_order(", "item_name=", "modifications=", "topping",
]

def check(scen_id, expect, reply, tool_calls, cart):
    """간단한 체크 — partial cover (substring matching). 실패 이유 enum."""
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
                "reply": reply[:120],
                "cart": [(it["menu"], it["quantity"], it.get("options", [])) for it in cart],
                "tools": [(tc["name"], tc.get("result", {}).get("status")) for tc in tcs],
            })
            print(f"  [{s_id}] {'✓' if not fails else '✗'} {msg[:50]}", file=sys.stderr)
        except Exception as e:
            results.append({"id": s_id, "cat": cat, "msg": msg, "pass": False, "fails": [f"EXCEPTION: {e}"], "reply": "", "cart": [], "tools": []})
            print(f"  [{s_id}] EXC {e}", file=sys.stderr)

    # summary
    print("\n=== SUMMARY ===")
    cats = {}
    for r in results:
        c = r["cat"]
        cats.setdefault(c, [0, 0])
        cats[c][1] += 1
        if r["pass"]: cats[c][0] += 1
    for c, (ok, total) in cats.items():
        bar = "█" * ok + "░" * (total - ok)
        print(f"  {c:10s} {ok}/{total}  {bar}")
    overall_ok = sum(1 for r in results if r["pass"])
    print(f"  {'TOTAL':10s} {overall_ok}/{len(results)}  ({100*overall_ok//len(results)}%)")

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
