"""임베딩 기반 메뉴 검색 (semantic retrieval).

목적:
1. 의미 매칭 — "단 거", "달콤한" 같은 발화를 메뉴와 연결 (키워드 매칭으로는 약함)
2. `inquire_menu_info` dispatcher가 실제 메뉴 정보를 답변할 수 있게 함

설계:
- 한국어 sentence embedding 모델 (jhgan/ko-sroberta-multitask)
- 메뉴 각각을 descriptor 문자열로 변환 → embedding
- 코사인 유사도로 top-k 검색
- 인덱스는 lazy-load + 디스크 캐시 (다음 실행 시 재인덱싱 스킵)

descriptor 보강에 쓰는 카테고리 설명/맛 태그는 menu_data.yaml에서 읽는다
(get_category_descriptor / item["tags"]) — 코드에 하드코딩하지 않는다.
"""
from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from app.cafe_profile import active_cafe
from app.domain.menu import MENU_DATA_PATH, get_category_descriptor, load_menu

logger = logging.getLogger("ediya.rag")

# 인덱스 캐시는 카페별로 분리 — 카페를 바꿔도 서로 캐시를 덮어쓰지 않는다.
INDEX_PATH = Path(__file__).resolve().parent / f"rag_index_{active_cafe()}.pkl"
EMBEDDING_MODEL_NAME = "jhgan/ko-sroberta-multitask"

# answer_menu_inquiry에서 "유의미한 매칭"으로 간주하는 코사인 유사도 하한.
# 이보다 낮으면 후보로 제시하되 fallback 경로를 탄다.
RELEVANCE_THRESHOLD = 0.3


def _menu_data_mtime() -> float:
    """menu_data.yaml의 최종 수정 시각. 캐시 무효화 판단에 쓴다."""
    try:
        return MENU_DATA_PATH.stat().st_mtime
    except OSError:
        return 0.0


def _menu_to_descriptor(item: Dict[str, Any]) -> str:
    """메뉴 1개를 embedding용 descriptor 자연어로 변환.

    구성: 메뉴명 + 카테고리 설명 + 가격대 + 맛 태그 + 온도/디카페인(메뉴명에서 유도).
    """
    parts = [item["kr"], get_category_descriptor(item.get("category", ""))]

    # 가격대
    price = item.get("base_price_l", 0)
    if price:
        if price < 4000:
            parts.append("저렴한")
        elif price < 5000:
            parts.append("적당한 가격")
        else:
            parts.append("프리미엄 가격")

    # 맛/속성 태그 (menu_data.yaml의 tags 필드 — 메뉴명에서 유도 불가한 속성)
    parts.extend(item.get("tags") or [])

    # 온도 (메뉴명에서 유도)
    if item["kr"].startswith("아이스") or "콜드" in item["kr"]:
        parts.append("차가운")
    if item["kr"].startswith("핫") or "따뜻" in item["kr"]:
        parts.append("따뜻한")

    # 디카페인
    if "디카페인" in item["kr"]:
        parts.append("카페인 없는")

    return " ".join(p for p in parts if p)


class MenuRetriever:
    """sentence-transformers 기반 메뉴 retriever. lazy-load."""

    def __init__(self, model_name: str = EMBEDDING_MODEL_NAME):
        self.model_name = model_name
        self._model = None
        self._index: Optional[Dict[str, Any]] = None

    def _load_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            logger.info(f"loading embedding model: {self.model_name}")
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def _build_index(self) -> Dict[str, Any]:
        menus = load_menu()["menus"]
        descriptors = [_menu_to_descriptor(m) for m in menus]
        model = self._load_model()
        embeddings = model.encode(descriptors, convert_to_numpy=True, show_progress_bar=False)
        # 정규화 (코사인 유사도용)
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        embeddings = embeddings / np.where(norms == 0, 1, norms)
        return {
            "menus": menus,
            "descriptors": descriptors,
            "embeddings": embeddings,
            "model_name": self.model_name,
            "menu_mtime": _menu_data_mtime(),
        }

    def _load_or_build(self):
        if self._index is not None:
            return
        if INDEX_PATH.exists():
            try:
                with INDEX_PATH.open("rb") as f:
                    self._index = pickle.load(f)
                # 모델 이름 또는 menu_data.yaml 변경 시 재구축
                if self._index.get("model_name") != self.model_name:
                    raise ValueError("model changed")
                if self._index.get("menu_mtime") != _menu_data_mtime():
                    raise ValueError("menu_data.yaml changed")
                logger.info(f"loaded RAG index from {INDEX_PATH}")
                return
            except Exception as e:
                logger.warning(f"index load failed ({e}), rebuilding")

        self._index = self._build_index()
        try:
            with INDEX_PATH.open("wb") as f:
                pickle.dump(self._index, f)
            logger.info(f"saved RAG index to {INDEX_PATH}")
        except Exception as e:
            logger.warning(f"index save failed: {e}")

    def search(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        """query와 의미적으로 가장 유사한 메뉴 top-k 반환.

        Returns:
            List of {menu_dict, score} where score is cosine similarity in [-1, 1].
        """
        self._load_or_build()
        if self._index is None:
            return []
        model = self._load_model()
        q_emb = model.encode([query], convert_to_numpy=True, show_progress_bar=False)
        q_norm = np.linalg.norm(q_emb, axis=1, keepdims=True)
        q_emb = q_emb / np.where(q_norm == 0, 1, q_norm)
        sims = (self._index["embeddings"] @ q_emb.T).flatten()
        top_idx = np.argsort(sims)[::-1][:k]
        return [
            {"menu": self._index["menus"][i], "score": float(sims[i])}
            for i in top_idx
        ]


# Singleton
_retriever: Optional[MenuRetriever] = None


def get_retriever() -> MenuRetriever:
    global _retriever
    if _retriever is None:
        _retriever = MenuRetriever()
    return _retriever


def search_menus(query: str, k: int = 5) -> List[Dict[str, Any]]:
    """편의 함수: 단일 query → top-k 메뉴 (score 포함)."""
    return get_retriever().search(query, k=k)


def answer_menu_inquiry(question: str, k: int = 5) -> Dict[str, Any]:
    """inquire_menu_info dispatcher에서 사용. RAG 검색 결과를 사용자 응답용으로 가공."""
    hits = search_menus(question, k=k)
    if not hits:
        return {"answer_basis": "no_match", "candidates": []}

    # 임계값 이상만 유의미한 매칭으로 — 없으면 점수 낮아도 상위 후보 제시
    relevant = [h for h in hits if h["score"] >= RELEVANCE_THRESHOLD][:5]
    if not relevant:
        relevant = hits[:3]

    return {
        "answer_basis": "rag_search",
        "candidates": [
            {
                "kr": h["menu"]["kr"],
                "category": h["menu"]["category"],
                "price_l": h["menu"].get("base_price_l"),
                "score": round(h["score"], 3),
            }
            for h in relevant
        ],
    }
