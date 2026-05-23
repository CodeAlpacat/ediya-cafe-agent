"""카페 도메인 시스템 프롬프트 로더.

프롬프트 본문은 app/cafes/<카페>/system_prompt.txt 에 있다 (카페별 콘텐츠).
도메인 규칙/few-shot을 코드에서 분리해 카페 교체를 쉽게 한다.
"""
from app.cafe_profile import load_system_prompt

SYSTEM_PROMPT = load_system_prompt()
