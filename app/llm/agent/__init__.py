"""LLM 에이전트 패키지.

`run_turn`은 first-call → tool dispatch → second-call 전체를 처리하고,
P0/P3/P4/P6 가드와 silent-add 복구까지 같은 루프에서 묶는다.

내부 구조:
- runner.py — AgentConfig + chat_once + run_turn 본 루프
- guards.py — silent corruption / stuck loop / confirm-question / silent-add 복구
- hints.py  — 발화 분석 + system hint 생성 (slang, Menu RAG, clarification, ...)

외부에서 import해야 하는 공개 API만 여기서 re-export한다.
"""
from app.llm.agent.runner import AgentConfig, chat_once, make_client, run_turn

__all__ = ["AgentConfig", "chat_once", "make_client", "run_turn"]
