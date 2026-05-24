"""정적 데모 페이지 mount.

`/`는 데모 HTML, `/static/*`는 보조 자원. static 폴더가 없으면 JSON 안내만.

데모 특성상 정적 파일은 자주 바뀌므로 캐시 hint를 약하게 — 운영이면 hash 기반
filename에 강한 cache로 가야 하지만, 여기서는 갱신 신뢰성이 더 중요.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_INDEX_HTML = _STATIC_DIR / "index.html"

_NO_CACHE = "no-cache, no-store, must-revalidate"

router = APIRouter(tags=["static"])


@router.get("/")
async def root():
    if _INDEX_HTML.exists():
        return FileResponse(
            str(_INDEX_HTML),
            headers={"Cache-Control": _NO_CACHE},
        )
    return {
        "service": "ediya-cafe-agent",
        "docs": "/docs",
        "note": "static demo page not bundled",
    }


class NoCacheStaticFiles(StaticFiles):
    """데모용 — 매 응답에 no-cache. 옛 CSS/JS가 브라우저 캐시에서 잡히는 사고 방지."""

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = _NO_CACHE
        return response


def mount_static(app: FastAPI) -> None:
    """`/static`을 mount. 폴더가 없으면 무시."""
    if _STATIC_DIR.exists():
        app.mount(
            "/static",
            NoCacheStaticFiles(directory=str(_STATIC_DIR)),
            name="static",
        )
