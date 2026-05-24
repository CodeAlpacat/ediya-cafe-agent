"""IntentProposal → 모델에 inject할 system message 텍스트.

NLU가 결정한 의도를 자연어로 풀어서 모델에게 1개 message로 전달한다.
누적 hint 8종 → 이 1개로 통합.
"""
from __future__ import annotations

from app.nlu.intent import IntentProposal, MenuIntent


_AMBIGUITY_NOTE = {
    "temperature": "온도(아이스/핫)가 안 정해졌어. 손님에게 짧게 되묻기. 도구 호출하지 마.",
    "menu_family": "메뉴 종류가 안 정해졌어. 후보 중 어떤 걸로 할지 손님에게 되묻기. 도구 호출하지 마.",
    "menu_unknown": "메뉴를 못 알아봤어. 손님에게 어떤 메뉴인지 되묻기. 도구 호출하지 마.",
    "option_missing": "손님이 요청한 옵션은 매장에서 제공하지 않아. 정직하게 안내. 도구 호출하지 마.",
    "decaf_hot": "이디야 디카페인은 콜드브루로만 가능해. '디카페인 콜드브루로 드릴까요?' 안내. 도구 호출하지 마.",
}


_ACTION_GUIDE = {
    "add": "→ add_menu 도구 호출",
    "replace": "→ replace_menu 도구 호출 (from_menu는 카트에서 찾아서)",
    "change_option": "→ change_option 도구 호출 (카트에 해당 메뉴 있을 때) 또는 add_menu (없을 때)",
    "remove": "→ remove_menu 도구 호출",
    "check_cart": "→ check_cart 도구 호출",
    "inquire": "→ inquire_menu_info 도구 호출",
    "done": "→ done 도구 호출",
    "undo": "→ undo 도구 호출",
    "ambiguous": "→ 도구 호출하지 말고 손님에게 명확화 질문",
    "unknown": "→ 발화를 다시 읽고 적절한 도구 호출 또는 명확화 질문",
}


def _format_intent(idx: int, intent: MenuIntent) -> str:
    lines = [f"  의도 {idx}:"]
    if intent.menu_kr:
        lines.append(f"    메뉴: {intent.menu_kr}")
    elif intent.ambiguity_candidates:
        cand = ", ".join(intent.ambiguity_candidates[:6])
        lines.append(f"    메뉴 후보: {cand}")
    else:
        lines.append("    메뉴: (NLU가 못 잡음 — 발화에서 직접 추출 시도)")
    lines.append(f"    수량: {intent.quantity}")
    if intent.options:
        lines.append(f"    옵션: {', '.join(intent.options)}")
    if intent.missing_options:
        missing_str = ", ".join(intent.missing_options)
        first_missing = intent.missing_options[0]
        lines.append(f"    매장에 없는 옵션: {missing_str} (제공 X)")
        lines.append(
            f"    응답 골조: \"죄송하지만 {first_missing}는 매장에서 제공하지 "
            f"않아요\" 형식으로 답변. 반드시 '{first_missing}'이라는 단어를 "
            "응답에 포함해서 무엇이 거절됐는지 분명히 알리기."
        )
    if intent.ambiguity:
        lines.append(
            f"    ⚠ 모호: {intent.ambiguity} — {_AMBIGUITY_NOTE.get(intent.ambiguity, '')}"
        )
    if intent.notes:
        for n in intent.notes:
            lines.append(f"    참고: {n}")
    return "\n".join(lines)


def render_proposal(proposal: IntentProposal) -> str:
    """IntentProposal → system message 본문.

    출력 형식:
        [NLU 의도 분석]
        행동: add
          의도 1:
            메뉴: 아이스아메리카노
            수량: 2
            옵션: 샷추가
        가이드: → add_menu 도구 호출
    """
    out = ["[NLU 의도 분석]", f"행동: {proposal.action}"]
    if proposal.intents:
        for i, intent in enumerate(proposal.intents, 1):
            out.append(_format_intent(i, intent))
    else:
        out.append("  (메뉴 의도 분해 없음 — 동작만 결정됨)")
    if proposal.global_notes:
        out.append("참고:")
        for n in proposal.global_notes:
            out.append(f"  - {n}")
    out.append(f"가이드: {_ACTION_GUIDE.get(proposal.action, '')}")

    # 모호 없는 의도 개수 — 있으면 즉시 도구 호출 강제 명령
    resolved = [
        i for i in proposal.intents
        if i.menu_kr is not None and i.ambiguity is None
    ]
    if resolved and proposal.action in ("add", "replace", "change_option"):
        # 복수면 parallel tool calls로 한 번에
        tool_name = {
            "add": "add_menu",
            "replace": "replace_menu",
            "change_option": "change_option",
        }[proposal.action]
        if len(resolved) == 1:
            r = resolved[0]
            opts = f', options={r.options}' if r.options else ', options=[]'
            out.append(
                f"⛔ 이 의도는 모호 없이 결정됐어. 텍스트로 '담을게요'라고 답하지 말고 "
                f"지금 즉시 다음 도구 호출해: "
                f"{tool_name}(menu=\"{r.menu_kr}\", quantity={r.quantity}{opts})"
            )
        else:
            calls = ", ".join(
                f"{tool_name}(menu=\"{r.menu_kr}\", quantity={r.quantity}, "
                f"options={r.options})"
                for r in resolved
            )
            out.append(
                f"⛔ {len(resolved)}개 의도 모두 모호 없이 결정됐어. 한 응답에 "
                f"parallel tool calls로 모두 호출해: {calls}"
            )

    out.append(
        "이 분석은 결정론적 사전처리 결과야. 위 정보를 신뢰하고 그대로 처리해. "
        "분석에 ⚠ 모호 표시가 없는 의도는 곧바로 도구 호출."
    )
    return "\n".join(out)
