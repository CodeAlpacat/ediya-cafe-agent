"""슬랭/동의어 사전 — 사용자 자연어 → 정식 메뉴/옵션.

profile.yaml의 slang_aliases와 카페 공통의 옵션 동의어를 한 곳에서 관리.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from app.domain.menu import detect_slang as _detect_slang_menu


# 옵션 표현 동의어 — 사용자가 자유롭게 쓰는 표현 → 정규 옵션명.
# 카페 공통 (특정 매장에 종속 X).
OPTION_ALIASES: Dict[str, str] = {
    # 샷 추가
    "샷": "샷추가",
    "샷추가": "샷추가",
    "샷 추가": "샷추가",
    "샷한번": "샷추가",
    "샷 한 번": "샷추가",
    "한 샷": "샷추가",
    "투샷": "투샷추가",
    "투샷추가": "투샷추가",
    "투 샷": "투샷추가",
    "샷 두번": "투샷추가",
    "샷 두 번": "투샷추가",
    "샷 2번": "투샷추가",
    "샷 2샷": "투샷추가",
    "샷두번": "투샷추가",
    "트리플샷": "트리플샷추가",
    "트리플 샷": "트리플샷추가",
    "트리플샷추가": "트리플샷추가",
    "샷 세번": "트리플샷추가",
    "샷 세 번": "트리플샷추가",
    "샷 3번": "트리플샷추가",
    # 사이즈
    "라지": "라지",
    "엑스트라": "엑스트라",
    "엑스트라사이즈": "엑스트라",
    "엑스트라 사이즈": "엑스트라",
    "엑사이즈": "엑스트라",
    "큰 사이즈": "엑스트라",
    "큰사이즈": "엑스트라",
    # 시럽
    "바닐라": "바닐라시럽 추가",
    "바닐라시럽": "바닐라시럽 추가",
    "바닐라 시럽": "바닐라시럽 추가",
    "헤이즐넛시럽": "헤이즐넛시럽 추가",
    "헤이즐넛 시럽": "헤이즐넛시럽 추가",
    "카라멜시럽": "카라멜시럽 추가",
    "카라멜 시럽": "카라멜시럽 추가",
    "흑당시럽": "흑당시럽 추가",
    "흑당 시럽": "흑당시럽 추가",
    # 휘핑
    "휘핑": "휘핑추가",
    "휘핑추가": "휘핑추가",
    "휘핑 추가": "휘핑추가",
    "휘핑빼기": "휘핑빼기",
    "휘핑 빼기": "휘핑빼기",
    "휘핑 빼주세요": "휘핑빼기",
    "휘핑없이": "휘핑빼기",
    # 당도
    "달게": "달게",
    "더 달게": "달게",
    "더달게": "달게",
    "덜달게": "덜달게",
    "덜 달게": "덜달게",
    "저당": "저당",
    "당도 낮게": "저당",
    # 얼음
    "얼음많이": "얼음많이",
    "얼음 많이": "얼음많이",
    "얼음적게": "얼음적게",
    "얼음 적게": "얼음적게",
    "얼음 빼": "얼음적게",
    "얼음빼": "얼음적게",
}


# 매장에 *없는* 옵션 (정직 거절 대상). NLU 단계에서 잡아 모델 헷갈림 방지.
KNOWN_UNAVAILABLE_OPTIONS = {
    "두유": "우유 변경 옵션",
    "오트밀크": "우유 변경 옵션",
    "오트 밀크": "우유 변경 옵션",
    "오트": "우유 변경 옵션",
    "저지방": "우유 변경 옵션",
    "락토프리": "우유 변경 옵션",
    "디카페인 옵션": "디카페인은 옵션이 아니라 별도 메뉴",
}


def detect_option_aliases(text: str) -> List[str]:
    """발화에 들어있는 옵션 표현 → 정규 옵션명 리스트.

    매칭 우선순위 (구체성 → 일반성):
    1. 트리플샷 계열 ("트리플샷", "샷 세 번/세번/3번")
    2. 투샷 계열 ("투샷", "샷 두 번/두번/2번")
    3. 그 외 옵션 (긴 키 먼저)
    구체성 매칭이 잡은 구간은 일반 매칭에서 제외해 "투샷"이 "샷"으로 추락하는 걸 막는다.
    중복 제거 + 등장 순서 유지.
    """
    if not text:
        return []
    found: List[tuple[int, str]] = []  # (start_pos, canonical)
    consumed = [False] * len(text)

    # 우선순위 그룹: 구체적인 것 먼저, 그룹 내부에서는 긴 키 먼저
    triple_keys = sorted(
        [k for k, v in OPTION_ALIASES.items() if v == "트리플샷추가"],
        key=lambda s: -len(s),
    )
    double_keys = sorted(
        [k for k, v in OPTION_ALIASES.items() if v == "투샷추가"],
        key=lambda s: -len(s),
    )
    other_keys = sorted(
        [k for k, v in OPTION_ALIASES.items()
         if v not in ("트리플샷추가", "투샷추가")],
        key=lambda s: -len(s),
    )

    for group in (triple_keys, double_keys, other_keys):
        for k in group:
            start = 0
            while True:
                idx = text.find(k, start)
                if idx < 0:
                    break
                if any(consumed[idx : idx + len(k)]):
                    start = idx + 1
                    continue
                for i in range(idx, idx + len(k)):
                    consumed[i] = True
                found.append((idx, OPTION_ALIASES[k]))
                start = idx + len(k)

    found.sort(key=lambda x: x[0])
    out: List[str] = []
    seen = set()
    for _, name in found:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def detect_missing_options(text: str) -> List[str]:
    """발화에 들어있는 *매장에 없는* 옵션 표현. 정직 거절용."""
    if not text:
        return []
    out: List[str] = []
    seen = set()
    keys_by_len = sorted(KNOWN_UNAVAILABLE_OPTIONS.keys(), key=lambda s: -len(s))
    for k in keys_by_len:
        if k in text and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def detect_menu_slang(text: str) -> Dict[str, List[str]]:
    """profile.yaml 등록 슬랭 매칭. menu 모듈 위임."""
    return _detect_slang_menu(text)
