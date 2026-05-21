"""RAG Step 2: Embedding-based menu retrieval.

목적:
1. P2 hallucination을 완전히 해결 (Step 1은 keyword 매칭 한계 — "단 거", "달콤한" 같은
   의미 매칭 약함)
2. `inquire_menu_info` dispatcher가 실제 메뉴 정보를 답변할 수 있게 함

설계:
- 한국어 sentence embedding 모델 (jhgan/ko-sroberta-multitask)
- 메뉴 각각을 descriptor 문자열로 변환 → embedding
- 코사인 유사도로 top-k 검색
- 인덱스는 lazy-load + 디스크 캐시 (다음 실행 시 재인덱싱 스킵)
"""
from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from app.menu import load_menu

logger = logging.getLogger("ediya.rag")

INDEX_PATH = Path(__file__).resolve().parent / "rag_index.pkl"
EMBEDDING_MODEL_NAME = "jhgan/ko-sroberta-multitask"


# 단맛/추천 같은 의미 키워드와 메뉴 매핑을 위한 descriptor enrichment
# 모델이 embedding으로 의미 매칭하도록 자연어 설명 추가
_CATEGORY_DESCRIPTORS = {
    "coffee": "커피 음료, 에스프레소 기반",
    "cold_brew": "콜드브루 커피, 깔끔하고 부드러운 맛",
    "decaf": "디카페인 커피, 카페인 없음",
    "beverage": "비커피 음료, 부드러운 맛",
    "tea": "차, 카페인 적거나 없음, 향긋한 맛",
    "bubble_tea": "버블티, 쫄깃한 펄 들어간 달콤한 음료",
    "flatccino": "플랫치노, 진하고 달콤한 블렌딩 음료",
    "ade": "에이드, 상큼하고 시원한 음료",
    "bakery": "베이커리, 빵/디저트",
    "ice_flakes": "빙수, 시원한 디저트",
}

# 메뉴별 맛 키워드 (특정 메뉴에만 의미적으로 강한 단어가 있는 경우)
_SWEET_KEYWORDS = [
    "바닐라라떼", "헤이즐넛라떼", "카페모카", "카라멜마끼아또", "화이트초콜릿모카",
    "연유라떼", "연유콜드브루", "흑당콜드브루",
    "달고나밀크", "흑당밀크",
    "초코라떼", "딸기라떼",
    "흑당버블티",
    "모카플랫치노",
    "모구모구",
    "단팥", "망고", "듀오초콜릿",
]


def _menu_to_descriptor(item: Dict[str, Any]) -> str:
    """메뉴 1개를 embedding용 descriptor 자연어로 변환."""
    parts = [item["kr"]]
    parts.append(_CATEGORY_DESCRIPTORS.get(item.get("category", ""), ""))

    # 가격대
    price = item.get("base_price_l", 0)
    if price:
        if price < 4000:
            parts.append("저렴한")
        elif price < 5000:
            parts.append("적당한 가격")
        else:
            parts.append("프리미엄 가격")

    # 맛 키워드 (메뉴명에 단맛 관련 어휘 있으면 보강)
    if any(sw in item["kr"] for sw in _SWEET_KEYWORDS):
        parts.append("달콤한 맛")

    # 온도
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
        }

    def _load_or_build(self):
        if self._index is not None:
            return
        if INDEX_PATH.exists():
            try:
                with INDEX_PATH.open("rb") as f:
                    self._index = pickle.load(f)
                # 모델 이름 변경 시 재구축
                if self._index.get("model_name") != self.model_name:
                    raise ValueError("model changed")
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

    # 상위 결과 중 score >= 0.3 만 유의미한 것으로
    relevant = [h for h in hits if h["score"] >= 0.3][:5]
    if not relevant:
        relevant = hits[:3]  # 점수 낮아도 일단 후보 제시

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
