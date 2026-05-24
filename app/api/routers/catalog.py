"""GET /menu/catalog — UI 카탈로그 패널 + 옵션 picker용 메뉴 카탈로그 JSON."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.schemas import (
    CatalogCategory,
    CatalogMenu,
    CatalogOption,
    CatalogOptionCategory,
    CatalogResponse,
)
from app.cafe_profile import load_profile
from app.dispatcher import _EXCLUSIVE_OPT_CATEGORIES  # 단일 진실 — 재선언 X
from app.domain.menu import load_menu

router = APIRouter(tags=["catalog"])


def _menu_to_catalog(m: dict) -> CatalogMenu:
    return CatalogMenu(
        kr=m["kr"],
        en=m.get("en"),
        category=m.get("category", ""),
        base_price_l=int(m.get("base_price_l") or 0),
        stock=m.get("stock") if m.get("stock") not in (-1, None) else None,
        tags=list(m.get("tags") or []),
    )


@router.get("/menu/catalog", response_model=CatalogResponse)
async def catalog() -> CatalogResponse:
    """전체 메뉴/옵션 사전 + 슬랭. 활성 카페(`CAFE_PROFILE`) 기준."""
    data = load_menu()
    profile = load_profile()

    # 카테고리별 메뉴 group + display order는 categories 순서대로
    cats_meta = data.get("categories", {})  # {cid: {descriptor, keywords, ...}}
    by_cat: dict[str, list[dict]] = {}
    for m in data["menus"]:
        by_cat.setdefault(m.get("category", ""), []).append(m)

    categories: list[CatalogCategory] = []
    for cid, meta in cats_meta.items():
        menus = [_menu_to_catalog(m) for m in by_cat.get(cid, [])]
        if not menus:
            continue
        categories.append(CatalogCategory(
            id=cid,
            descriptor=meta.get("descriptor"),
            keywords=list(meta.get("keywords") or []),
            menus=menus,
        ))

    option_cats: list[CatalogOptionCategory] = []
    for oc in data["option_categories"]:
        option_cats.append(CatalogOptionCategory(
            kr=oc["kr"],
            en=oc.get("en"),
            applicable_categories=oc.get("applicable_categories"),
            is_exclusive=oc["kr"] in _EXCLUSIVE_OPT_CATEGORIES,
            options=[CatalogOption(
                kr=o["kr"], en=o.get("en"), price_delta=int(o.get("price_delta") or 0)
            ) for o in oc["options"]],
        ))

    return CatalogResponse(
        cafe_name=profile.get("cafe_name", "이디야커피"),
        categories=categories,
        option_categories=option_cats,
        slang_aliases={k: list(v or []) for k, v in (profile.get("slang_aliases") or {}).items()},
    )
