"""발화 정규화 + 수량 추출.

순수 함수. 결정론. 단위 테스트로 완전 검증.
"""
from __future__ import annotations

import re

# 한글 수 표현 → 정수
_KO_QUANTITY = {
    "한": 1, "하나": 1,
    "두": 2, "둘": 2,
    "세": 3, "셋": 3,
    "네": 4, "넷": 4,
    "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10,
}

_QTY_DIGIT = re.compile(r"(\d+)\s*(잔|개|컵|개씩|잔씩)?")


def normalize_whitespace(text: str) -> str:
    """양끝/중간 다중 공백을 정리. 한글-한글 사이 1칸만."""
    return re.sub(r"\s+", " ", text).strip()


def extract_quantity(text: str) -> int:
    """발화에서 수량 추출. 못 찾으면 1.

    우선순위:
    1) "2잔" / "3개" 같은 숫자 + 단위
    2) "두 잔" / "두잔" 같은 한글 수 + 단위
    3) "한" 같은 한글 수 단독
    """
    if not text:
        return 1

    m = _QTY_DIGIT.search(text)
    if m and m.group(2):  # 숫자 + 단위
        return max(1, int(m.group(1)))

    for word, n in _KO_QUANTITY.items():
        # 단위 동반: 더 강한 신호
        if re.search(rf"\b{word}\s*(잔|개|컵)", text):
            return n
        if f"{word}잔" in text or f"{word}개" in text or f"{word}컵" in text:
            return n

    # 단독 한글 수 (단위 없이) — 단어 경계 다음에 (공백|에|만|로|끝)이 와야 양사로 본다.
    # '한' 같은 단어가 본 단어(예: "한국") 안에 들어가지 않도록.
    for word, n in _KO_QUANTITY.items():
        if re.search(rf"(^|\s){word}(\s|에|만|로|$)", text):
            return n

    # 숫자 단독 (단위 없음)
    if m and not m.group(2):
        v = int(m.group(1))
        if 1 <= v <= 20:
            return v

    return 1
