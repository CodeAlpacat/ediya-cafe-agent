# Implementation Plan: LLM-first 재설계

**Status**: 🔄 In Progress
**Started**: 2026-05-25
**Branch**: `refactor/llm-first` (base: c9df469 of `refactor/cafe-profile-and-guards`)
**Archive**: `archive/nlu-pattern-approach` (NLU 사전화 시도 — 보존만)

---

## 📋 Overview

### Why
사용자 의도: *production 카페 주문/결제 에이전트는 LLM의 언어 이해를 주축으로 설계*. 패턴매칭 사전 누적은 메뉴/카페 변경 시 기술 부채 (코드 PR). 점수 낮아도 **유연성 + 확장성**이 우선.

### What's wrong with previous approach (NLU 사전화, c9df469)
- 슬랭/옵션 별칭/도메인 룰을 `app/nlu/`에 코드로 박음
- 새 슬랭("얼죽아")·신메뉴·새 카페 → 코드 변경 필수
- eval 점수 83% — 그러나 사전 안 영역의 점수. 사전 밖 발화는 측정 자체 X
- 룰 6 회피 trigger: *"검증으로 contain"* 의 변형 — 패턴 사전으로 contain

### LLM-first 원칙
1. **메뉴는 데이터, 코드 아님**. YAML이 single source of truth. LLM context로 dynamic inject.
2. **도메인 룰은 system prompt에 자연어로**. 코드 lock-in X. ("디카페인은 콜드브루로만" → 한국어 한 문장)
3. **슬랭 추론은 LLM에 위임**. 사전 누적 X. 모델이 못 잡으면 자연스럽게 되묻기.
4. **dispatcher는 데이터 검증만** — 메뉴 실재 / 옵션 사전 / 재고. 의미 추론 X.
5. **eval은 real-user 발화 다양화** — 사전 거울 X. 진짜 점수 측정.

### Success Criteria
- [ ] `app/nlu/` 폴더 0 (또는 dispatcher 검증만 남김)
- [ ] system_prompt LLM-first 재설계 — 도메인 룰을 한국어로 명시, ~40줄
- [ ] 메뉴 데이터를 매 턴 context에 dynamic inject (RAG ladder 활용)
- [ ] eval real-user 발화 50+ 다양화 (사전 외 슬랭/오타/부정/추론 카테고리 포함)
- [ ] 단위 테스트 (도메인 데이터 검증) 통과
- [ ] 새 baseline 정직히 측정 — 점수 낮아도 OK
- [ ] **메뉴 변경 robust 테스트**: menu_data.yaml 1줄 추가하면 신메뉴 주문 받아짐 (코드 변경 X)

### Trade-off 인정
- 점수: NLU 83% → LLM-first 예상 60-75% (작은 모델 한계)
- 응답시간: 동일 (5-10초)
- 유연성: 메뉴/카페/슬랭 변경 → 코드 변경 0
- 비용: 동일 ($0, 로컬 ollama)

작은 모델로 LLM-first의 천장은 70%대 — 큰 모델로 가면 90%+. 현 단계는 *설계 기반 마련*. 모델 업그레이드는 별도 결정.

---

## 🏗️ Architecture Decisions

| Decision | Rationale |
|----------|-----------|
| `app/nlu/` 전체 삭제 | 패턴 사전 누적 = 메뉴 변경에 lock-in. LLM-first 정신 위배. |
| 슬랭/옵션 별칭/도메인 룰 → system prompt 자연어 | 데이터/룰을 자연어로 LLM에 전달 — 메뉴 변경 시 prompt 또는 YAML만 |
| 메뉴 YAML을 매 턴 system msg에 dynamic inject | LLM이 메뉴 사전 모름 → context로 박음 |
| 가드 거의 다 제거, stuck_loop만 유지 | 가드 누적은 또 한계 — LLM 신뢰 |
| second-call (자연어 응답) 유지 | tool result를 한국어로 푸는 것 — LLM 강점 |
| eval real-user 다양화 — 사전 외 발화 포함 | 점수 조작 방지. 진짜 production 측정 |

---

## 🚀 Phases

### Phase 1: NLU 제거 + 가드 청소 + system prompt LLM-first 재설계 (3h)

#### Tasks
- [ ] **1.1** `app/nlu/` 폴더 통째 삭제
- [ ] **1.2** `app/tests/test_nlu.py` 삭제 (NLU 자체가 없어짐)
- [ ] **1.3** `app/llm/agent/proposal_renderer.py` 삭제 (NLU 의존)
- [ ] **1.4** `runner.py` 단순화 — NLU 호출 제거, dynamic menu inject 추가
- [ ] **1.5** `system_prompt.txt` 재설계 (~40줄):
  - 도메인 룰 자연어 (디카페인 콜드브루, 라지 기본, 아이스/핫 메뉴 일부)
  - few-shot 3-4개 (슬랭/온도 누락/디카페인/모호)
  - "매 턴 [메뉴 데이터] 컨텍스트가 제공된다" 명시
- [ ] **1.6** dynamic menu context builder — 매 턴 system msg로 inject:
  - 발화 관련 RAG 후보 5-8개 (이미 있는 `keyword_menu_search` + `rag.search_menus`)
  - 옵션 카테고리 사전 (사이즈/샷/시럽/휘핑/당도/얼음)
  - 매장 미보유 항목 명시 (두유/오트밀크 — 정직 거절용)
- [ ] **1.7** dispatcher는 **데이터 검증만** — 의미 추론 (디카페인 매핑 등) 모두 제거. 신뢰는 LLM에.
  - 단, *오타 보정*: `find_similar_menus` 기존 활용 — 모델 답을 받았는데 메뉴명이 약간 다르면 후보 제시

#### Gate
- [ ] `app/nlu/` 0개 파일
- [ ] system_prompt ~40줄, 자연어 룰만
- [ ] `make test` (non-ollama) 통과 — NLU 테스트는 제거됨

---

### Phase 2: eval real-user 다양화 (2h)

#### Tasks
- [ ] **2.1** `scripts/eval.py` 시나리오 재작성 — 50+ 발화, 다음 카테고리 균등 분포:
  - A. **사전 안 정통 발화** (10개) — "아이스 아메리카노 한 잔"
  - B. **흔한 슬랭** (5개) — "아아", "아샷추" + 새 슬랭 "얼죽아", "아바라"
  - C. **다양 옵션 표현** (8개) — "엑스트라로", "샷 두번", "조금 덜 단", "달지 않은", "얼음 조금만"
  - D. **매장에 없는 옵션** (5개) — "두유", "오트밀크", "저지방", "디카페인 옵션" (메뉴X)
  - E. **복합/접속** (5개) — "X랑 Y", "X 하나에 Y 하나", "X 빼고 Y 추가"
  - F. **모호** (5개) — 메뉴 family, 온도 누락, "커피 한 잔"
  - G. **변경/취소/undo** (5개) — replace, remove, undo
  - H. **문의/추천** (5개) — 가격, 추천, 메뉴 있냐
  - I. **컨텍스트 (multi-turn)** (5개) — "아까 그거", "방금 시킨 거 빼줘"
  - J. **추론/감정** (5개) — "단 거 말고 안 단 거", "베스트가 뭐예요", "추천 좀"
  - K. **오타/구어체** (5개) — "아매리카노", "라떼ㅔ 두잔", "아이스 아 메 리카노"
- [ ] **2.2** 채점 함수 강화 — 단순 substring 매칭의 한계 보완. 모호 발화는 *되묻는지*만 검증, cart 안 변하는 게 정답.
- [ ] **2.3** **메뉴 변경 robust 테스트** — 신메뉴 1개를 menu_data.yaml 임시 추가하고 "민트프라푸치노 한 잔" 발화 → cart에 들어가는지

#### Gate
- [ ] eval 50+ 시나리오
- [ ] 채점 함수가 모호 발화도 정확히 평가
- [ ] 메뉴 변경 robust 테스트 1개 통과

---

### Phase 3: 측정 + 회귀 fix + push (2h)

#### Tasks
- [ ] **3.1** eval 3회 측정 — 변동성 확인
- [ ] **3.2** 회귀 항목 분류:
  - 모델 한계 (작은 모델 자유발화) → 인정, 모델 업그레이드 영역
  - prompt 부족 → 자연어 룰 1-2줄 추가 (band-aid 아님 — *명시 부족이었던 룰*)
  - RAG 약함 → ko-sroberta 인덱스 활용 강화
- [ ] **3.3** README 갱신 — LLM-first 설계 + trade-off (점수 vs 유연성) + 모델 업그레이드 옵션
- [ ] **3.4** commit + push (refactor/llm-first → origin)
- [ ] **3.5** PR 생성 (선택)

#### Gate
- [ ] 3회 측정 변동성 ≤ ±5%p
- [ ] 정직한 baseline 박힘
- [ ] 메뉴 변경 robust 테스트 통과
- [ ] README의 모든 명령 작동

---

## ⚠️ Risk

| Risk | P | Impact | Mitigation |
|------|---|--------|-----------|
| LLM-first 점수가 NLU 83%보다 *크게* 낮음 (50% 미만) | M | M | 정직히 보고. 모델 업그레이드 옵션 명시. 사용자 의도("타율 낮아도 OK") 확인 받음. |
| dynamic menu inject가 context too long → 응답 느려짐 | M | L | RAG로 후보 5-8개만 inject. 전체 메뉴 X. |
| dispatcher 검증만 남기면 "디카페인 아메리카노" 같은 발화가 INVALID_MENU 거절 | H | M | dispatcher가 INVALID_MENU 응답에 *유사 메뉴 후보* 포함 → 모델이 사용자에게 안내. (이미 `find_similar_menus` 있음) |
| 사용자가 점수 너무 낮으면 다시 NLU 돌리자 할 수 있음 | L | M | Plan 끝에 데이터 박힘. archive branch에 NLU 보존 → 언제든 비교/복원 가능. |

---

## 🔄 Rollback

각 phase 끝에 commit. 회귀 심하면 직전 phase로 reset. 최악의 경우 `git checkout archive/nlu-pattern-approach`로 NLU 복원.

---

## 📝 Notes

### 최종 결과 (Phase 1 + 2 + 3 완료, 2026-05-25)

| eval set | 점수 | 변동성 (3회) |
|----------|------|------------|
| 30 시나리오 (사전 거울) — NLU 사전화 직전 | 25/30 (83%) | 0 |
| 30 시나리오 — LLM-first Phase 1 직후 | 24/30 (80%) | — |
| **60 시나리오 (real-user 다양화) — LLM-first 최종** | **57/60 (95%)** | **0** |

**핵심**: 코드 -800줄 + 메뉴/카페 변경 robust + *진짜 자연어 다양화* eval로 95%.

### 남은 3건 (real model limit, gemma4:e2b)

| ID | 발화 | 원인 |
|----|------|------|
| B1 | "아아 한 잔" | 슬랭 사전 명시했는데 모델이 "아이스/핫?" 되묻기 — 작은 모델 한국어 slot filling 약점 |
| E1 | "아아 하나랑 핫 카페라떼 하나" | 복합 발화 silent tool call (모델이 자연어로만 응답, parallel API 미호출) |
| K1 | "아아 샷추가 + 아이스 라떼 두유" | 두 의도 중 두유는 거절 OK, 아아 샷추가는 silent |

→ 진짜 *작은 모델 + 한국어 tool calling*의 한계. 더 올리려면 모델 사이즈 (Claude haiku 등). LLM-first 설계 자체는 큰 모델에서 효용 ↑.

### Phase별 변경 요약

폐기/변경:
- `app/nlu/` 통째 삭제 (-553줄)
- `app/llm/agent/proposal_renderer.py` 삭제
- `app/tests/test_nlu.py` 삭제
- `system_prompt.txt`: 17줄 → 36줄 (LLM-first 자연어 룰)
- `runner.py`: NLU 호출 제거, `build_menu_context` dynamic inject

신설:
- `app/llm/menu_context.py` — 매 턴 [메뉴 후보 + 옵션 사전 + 슬랭 사전 + 매장 미보유] LLM context에 dynamic inject
- `guards.py`: stuck_loop + tool_hallucination (1개 critical 가드, 누적 X)
- `scripts/eval.py`: 30 → 60 시나리오 (실 유저 다양화)

### Plan 종료

목표 80%+ 달성 (60 시나리오 95%, 변동성 0). 90 unit tests pass. LLM-first 설계 안착.

---

### Phase 1 완료 (2026-05-25)

폐기/변경:
- `app/nlu/` 통째 삭제 (-553줄)
- `app/llm/agent/proposal_renderer.py` 삭제
- `app/tests/test_nlu.py` 삭제
- `system_prompt.txt`: 17 → 35줄 (LLM-first 자연어 룰)
- `runner.py`: NLU 호출 제거, `build_menu_context` dynamic inject

신설:
- `app/llm/menu_context.py` — 매 턴 [메뉴 후보 + 옵션 사전 + 슬랭 사전 + 매장 미보유] inject
- `guards.py`: stuck_loop + tool_hallucination (1개 critical 가드, 누적 X)

eval (현 30 시나리오 — 사전 거울 한계 있음):
- NLU 사전화 (직전 c9df469): **25/30 (83%)**
- LLM-first (Phase 1): **24/30 (80%)** ← -3%p
- 코드 -800줄, 메뉴/카페 robust, 모델 자유도 ↑

→ 거의 동급. *진짜 효용*은 Phase 2 다양화 eval로 측정.


