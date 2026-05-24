"""NLU 사전처리 — 결정론적 의도 분석.

발화를 IntentProposal로 변환. 슬랭/온도/디카페인/옵션 매핑은 모두 여기서 결정.
모델은 IntentProposal를 받아서 tool_call 또는 clarify_question 응답만 한다.
"""
from app.nlu.intent import (
    AmbiguityKind,
    IntentProposal,
    MenuIntent,
    ActionHint,
)
from app.nlu.extractor import extract_intent

__all__ = [
    "ActionHint",
    "AmbiguityKind",
    "IntentProposal",
    "MenuIntent",
    "extract_intent",
]
