"""카페 프로필 로더 — 카페별 콘텐츠(메뉴/프롬프트/줄임말)를 한 곳에서 해석.

카페 교체는 `app/cafes/<이름>/` 폴더 추가 + `CAFE_PROFILE` 환경변수 지정으로 끝난다.
각 폴더는 menu_data.yaml / system_prompt.txt / profile.yaml 세 파일을 갖는다.
도메인 규칙·few-shot은 system_prompt.txt에, 메뉴는 menu_data.yaml에 들어 있어
코드에는 카페별 하드코딩이 없다.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import yaml

from app.core.config import get_settings

CAFES_DIR = Path(__file__).resolve().parent / "cafes"


def active_cafe() -> str:
    """현재 활성 카페 이름. `Settings.cafe_profile` 값(기본 'ediya')."""
    return get_settings().cafe_profile.strip() or "ediya"


def cafe_dir() -> Path:
    """활성 카페의 콘텐츠 폴더 경로."""
    path = CAFES_DIR / active_cafe()
    if not path.is_dir():
        raise FileNotFoundError(
            f"카페 프로필 폴더가 없습니다: {path} "
            f"(CAFE_PROFILE={active_cafe()}). app/cafes/ 아래에 폴더를 만드세요."
        )
    return path


def menu_data_path() -> Path:
    """활성 카페의 menu_data.yaml 경로."""
    return cafe_dir() / "menu_data.yaml"


def system_prompt_path() -> Path:
    """활성 카페의 system_prompt.txt 경로."""
    return cafe_dir() / "system_prompt.txt"


@lru_cache(maxsize=None)
def load_profile() -> Dict[str, Any]:
    """활성 카페의 profile.yaml 로드 (카페명 + 줄임말 맵 등)."""
    with (cafe_dir() / "profile.yaml").open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_system_prompt() -> str:
    """활성 카페의 시스템 프롬프트 전문."""
    return system_prompt_path().read_text(encoding="utf-8")
