# Implementation Plan: NLU 사전처리로 가드 누적 구조 해소

**Status**: 🔄 In Progress
**Started**: 2026-05-25
**Branch**: `refactor/cafe-profile-and-guards`
**Baseline (가드 풀가동)**: 16/30 (53%)  ← 다른 머신 73%에서 회귀

---

## 📋 Overview

### Problem
현재 흐름: `발화 → 8 hint inject → 자유 LLM → 4 guard postprocess → dispatcher`.
가드/hint가 변형마다 누적 → 가드끼리 충돌 → silent corruption guard가 폴백 문구로 정상 시나리오까지 깸 (B1 "아아 한 잔").

이건 **설계 회피 trigger** (haven-chat CLAUDE.md 룰 6): *"검증/프로세스로 contain"*.
LLM 비결정성을 *결정론 영역*까지 위임한 게 원인 — 슬랭/온도/디카페인/옵션 사전은 100% 결정론인데 모델에게 시키고 가드로 뒤따라가는 중.

### Solution — 불변식 기반 설계 (band-aid 금지)
모델의 자유 영역을 좁힌다. 결정론은 코드, LLM은 *"의도 분류 + slot 채우기"* 1단계만.

```
[현재]                           [목표]
발화                              발화
 → 8 hint inject                  → NLU (결정론, 코드)
 → 자유 LLM                       → IntentProposal 1개 system msg
 → 4 guard postprocess            → 좁은 LLM (tool_call OR clarify_question)
 → dispatcher                     → dispatcher
```

### 핵심 불변식
- **I1**: 슬랭/온도/디카페인/옵션 매핑은 **코드에서만** 결정한다. 모델은 모르는 일.
- **I2**: 모델의 입력에는 **정규화된 IntentProposal**만 들어간다. 누적 hint 금지.
- **I3**: 모델의 출력은 **tool_call** 또는 **clarify_question** 둘 중 하나. 자유 자연어 응답 금지 (단, tool result 받은 다음 second-call에서만 자연어 허용).

이 3개가 지켜지면 14건 실패 클래스가 *구조적으로* 발생 불가.

### Success Criteria
- [ ] eval 30개 ≥ **24/30 (80%)** — 회귀 0
- [ ] guards.py LoC < 80 (현 228) — silent-add recovery / tool hallucination 가드 제거
- [ ] hints.py LoC < 50 (현 211) — slang/RAG hint는 NLU로 이전, 나머지 삭제
- [ ] system_prompt.txt ≤ 40줄 (현 113) — 도메인 규칙은 NLU가 결정하므로 prompt에서 제거
- [ ] NLU 단위 테스트 ≥ 30개 (결정론 영역은 unit test로 완전 검증 가능)
- [ ] `make test` (non-ollama) 전체 통과

---

## 🏗️ Architecture Decisions

| Decision | Rationale | Trade-off |
|----------|-----------|----------|
| NLU 전처리 layer 신설 (`app/nlu/`) | 결정론을 모델에서 분리. 단위 테스트로 100% 커버 가능 | 새 모듈 — 학습 곡선 |
| IntentProposal를 system msg로 inject (별도 schema 강제 X) | E2B에서 structured output 안정성 떨어짐. 자연어 hint로 박는 게 안정적 | 모델이 100% 따른다고 보장 못 함 → eval로 검증 |
| 가드를 *전부 제거* 하지 않음 (stuck loop만 유지) | stuck loop은 무한 round trip 방지 안전망. 다른 가드는 모델 좁힐 때 자연 소멸 | "혹시 모르니 유지"는 룰 6 anti-pattern — 효용 측정으로 결정 |
| slang_aliases를 YAML profile에 유지 | 카페별 교체 가능성 살림 | YAML 1군데 외 코드도 슬랭 정규화 함 (`아샷추` 등 동의어) |
| second-call (tool result → 자연어) 그대로 둠 | dispatcher 결과 자연어로 풀어주는 건 LLM 적합 영역 | 변화 없음 |

---

## 🧪 Test Strategy

NLU는 결정론 → 단위 테스트로 끝낸다. eval은 E2E.

| 레이어 | 도구 | 목표 |
|-------|------|------|
| NLU 단위 (`app/tests/test_nlu_*.py`) | pytest, mock 없음 (순수 함수) | ≥30 케이스, 100% pass |
| API 통합 (`test_api.py`) | TestClient + mock agent | 회귀 0 |
| E2E (eval.py) | live ollama | 24/30 ≥ |
| dispatcher / cart / menu / rag | 기존 90 tests | 회귀 0 |

---

## 🚀 Phases

### Phase 0: 가드 OFF baseline 측정 (30m) ✅
**Goal**: 가드의 실효성 데이터 확보.

**Tasks** (완료)
- [x] `_inject_hints` 통째 noop + silent_corruption / hallucination / confirm / silent-add recovery 모두 OFF
- [x] uvicorn server-side monkey-patch로 적용 (`/tmp/phase0_server.py`)
- [x] eval 측정

**결과 (2026-05-25)**
| 모드 | 점수 |
|------|------|
| 가드 ON (전체 작동) | 16/30 (53%) |
| 가드 OFF (전부 우회) | **16/30 (53%)** ← 동일 |
| 작업자 baseline (다른 머신) | 22/30 (73%) |

→ **결론**: 가드 8 + hint 8은 이 환경 점수에 0건 기여. 작업자 baseline 73% 차이는 가드 효과가 아니라 머신/세션 variance.
→ Phase 3 청소의 정당성 데이터 확보 완료.

**Gate** ✅
- [x] 데이터 박힘
- [x] patch는 외부 파일(`/tmp/`) only — 코드 영구 변경 없음

---

### Phase 1: NLU 모듈 (3h)
**Goal**: 결정론 영역을 `app/nlu/`로 모은다.

#### 모듈 구조
```
app/nlu/
├── __init__.py
├── normalize.py       # 띄어쓰기/공백/온도 표현 정규화
├── aliases.py         # 슬랭 (profile.yaml + 코드 사전)
├── domain_rules.py    # 디카페인 → 콜드브루, 사이즈 기본값, 옵션 매핑
├── menu_resolver.py   # 발화 → 메뉴 후보 (정확 매칭 + RAG fallback)
├── options.py         # 옵션 사전 lookup ("두유", "오트밀크" → 미보유)
├── intent.py          # IntentProposal dataclass + 분해
└── extractor.py       # 전체 파이프라인 entry: utterance → IntentProposal
```

#### IntentProposal schema
```python
@dataclass
class MenuIntent:
    menu_kr: Optional[str]              # 확정 메뉴. None이면 ambiguity 참고
    quantity: int
    options: List[str]
    missing_options: List[str]          # 사용자 발화에 있었으나 매장에 없는 옵션
    ambiguity: Optional[str]            # "temperature" | "menu_family" | None
    ambiguity_candidates: List[str]     # 모호할 때 후보들

@dataclass
class IntentProposal:
    action_hint: str                    # "add" | "change_option" | "remove" | "replace" | "inquire" | "done" | "undo" | "ambiguous"
    intents: List[MenuIntent]           # 복합 발화면 N개
    raw_utterance: str
    notes: List[str]                    # NLU가 가공한 메모 ("디카페인 → 콜드브루 매핑 적용")
```

#### Tasks (RED → GREEN)
- [ ] **R1.1** `test_nlu_normalize.py` (5 케이스): 띄어쓰기/숫자/한글-숫자 ("두 잔"→qty=2, "하나"→qty=1)
- [ ] **R1.2** `test_nlu_aliases.py` (8 케이스): 아아, 아샷추, 아바라, 라떼-default, decaf alias…
- [ ] **R1.3** `test_nlu_domain_rules.py` (6 케이스): 디카페인 핫 → 거절, 디카페인 아메리카노 → 콜드브루, 엑스트라 → options
- [ ] **R1.4** `test_nlu_options.py` (5 케이스): 두유/오트밀크 → missing_options, 샷추가 → 정상, 투샷추가 → 배타
- [ ] **R1.5** `test_nlu_menu_resolver.py` (6 케이스): "아이스 카페" → 후보=["아이스카페라떼"], 정확 매칭, RAG fallback
- [ ] **R1.6** `test_nlu_extractor.py` (10+ 케이스): eval 14 실패 케이스를 NLU 입력으로 던졌을 때 올바른 IntentProposal 나오는지

- [ ] **G1.7** normalize.py 구현
- [ ] **G1.8** aliases.py (profile.yaml 로드 + 추가 슬랭)
- [ ] **G1.9** domain_rules.py
- [ ] **G1.10** options.py
- [ ] **G1.11** menu_resolver.py (기존 `app.domain.menu.search_menus_for_utterance` + `app.llm.rag` 재사용)
- [ ] **G1.12** intent.py + extractor.py

#### Gate
- [ ] `make test` non-ollama 90 + NLU 30+ 모두 통과
- [ ] 14 실패 시나리오를 extractor에 직접 넣어 보면 올바른 IntentProposal 나옴

---

### Phase 2: IntentProposal → 좁은 prompt (2h)
**Goal**: runner가 NLU 결과를 system msg에 박고, system prompt를 압축.

#### Tasks
- [ ] **2.1** `app/llm/agent/proposal_renderer.py` 신규: `IntentProposal → system_msg_text`
  - 예시:
    ```
    [의도 분석]
    행동: add
    의도 1: 메뉴=아이스아메리카노, 수량=1, 옵션=[]
    참고: "아아"는 아이스아메리카노 줄임말로 해석함.

    위 의도가 맞으면 add_menu 호출. 다르면 clarify.
    ```
- [ ] **2.2** `runner.run_turn` 수정: hint 8개 inject 대신 `IntentProposal` 1개만 inject
- [ ] **2.3** `cafes/ediya/system_prompt.txt` 30줄로 축약 — 도메인 규칙 제거 (NLU가 처리), few-shot도 1-2개로
- [ ] **2.4** 모호한 경우 (ambiguity 있음) 처리: IntentProposal에 "되묻기 가이드" 포함

#### Gate
- [ ] eval 30개 측정 — Phase 0 OFF baseline 보다 ≥
- [ ] 단순 시나리오 (A,B,C,E) 100% 통과 — NLU가 결정론으로 결정한 것

---

### Phase 3: 청소 (1.5h)
**Goal**: NLU가 흡수한 가드/hint 제거. runner 짧게.

#### Tasks
- [ ] **3.1** `hints.py`에서 슬랭/RAG/clarification/compound/post-inquiry/referential hint 제거. `build_referential_hint`만 남길지 결정 (cart 마지막 항목 inject는 여전히 유용)
- [ ] **3.2** `guards.py`에서 silent-add recovery, tool hallucination guard 제거. `is_stuck_loop`만 유지
- [ ] **3.3** `runner.py`에서 dead code 제거 → 100-150줄로 압축
- [ ] **3.4** system_prompt.txt 폐기된 룰 7,8 제거 확인
- [ ] **3.5** import 정리

#### Gate
- [ ] LoC 목표 달성 (guards <80, hints <50, prompt ≤40)
- [ ] `make test` 통과
- [ ] eval 점수 Phase 2 대비 회귀 없음

---

### Phase 4: eval + 회귀 fix (2h)
**Goal**: 80%+ 안정. 실패 항목 NLU 사전/룰로 추가 흡수.

#### Tasks
- [ ] **4.1** eval 3회 측정 (변동성 확인)
- [ ] **4.2** 회귀 항목 분류 — NLU 사전 추가로 풀리는가, 도메인 룰 추가 필요한가, 모델 한계인가
- [ ] **4.3** 사전/룰 보강 (band-aid 아닌 일반화된 형태로)
- [ ] **4.4** README의 가드 5종 표 → "NLU + 좁은 모델 schema" 설계로 갱신

#### Gate
- [ ] 3회 평균 ≥ 80%
- [ ] 변동성 ≤ ±5%p (안정성)
- [ ] README 반영

---

## ⚠️ Risk

| Risk | P | Impact | Mitigation |
|------|---|--------|-----------|
| NLU가 의도 잘못 추출해서 모델이 거기에 끌려감 | M | H | extractor 30+ 단위 테스트로 강제. eval에서 fail → 사전/룰 보강 |
| E2B가 좁은 IntentProposal hint도 무시하고 자유 응답 | M | H | 룰 6 trigger 발동 — 가드로 재차 contain하지 말고 prompt 짧게 + IntentProposal를 user message 직전에 inject. 추가로 한국어로 *명령형* ("아래 의도대로 처리:") 박기 |
| eval baseline 73%가 실제로는 가드 효과가 아닌 모델 일진 운빨 | H | M | Phase 0에서 가드 OFF로 측정 → 데이터 확보 |
| second-call(자연어 응답) 깨지면 사용자 경험 회귀 | L | M | second-call은 그대로 둠 — 변경 없음 |

---

## 🔄 Rollback

각 phase 끝에 commit. 회귀 시 직전 phase로 reset.

---

## 📝 Notes

### Phase별 누적 결과

| Phase | 조치 | eval |
|-------|------|------|
| baseline (refactor 브랜치) | 가드 8 + hint 8 풀가동 | 16/30 (53%) |
| Phase 0 | 가드 + hint 모두 OFF | **16/30 (53%)** — 동일 |
| Phase 2 | NLU 도입 + 좁은 prompt | 21/30 (70%) |
| Phase 4 final (3회) | NLU 보강 + cart 상태 반영 | **25/30 (83%)** — 변동성 0 |

**핵심 발견**: 가드 8 + hint 8은 이 환경에서 점수 0건 기여 (Phase 0 데이터). 작업자 baseline 73%는 운빨 — 가드의 contain 효과가 *없다*는 게 데이터로 입증됨.

### LoC 감축

| 파일 | before | after |
|------|--------|-------|
| guards.py | 228 | 31 (is_stuck_loop만) |
| hints.py | 211 | **삭제** |
| system_prompt.txt | 113 | 17 |
| runner.py | 349 | 209 |
| 신규 (app/nlu/) | 0 | ~500 (결정론, 47 단위 테스트) |

**순감**: 가드/hint 코드 -549줄 + system prompt -96줄. 신규는 *결정론*이라 단위 테스트로 100% 검증 가능.

### 남은 5건 (real model limit)

| ID | 패턴 | NLU 분석 | 모델 응답 | 한계 |
|----|------|---------|----------|------|
| C2 | "아아에 샷추가" | add 확정, ⛔ 명령 | "추가했어요" 자연어만 | tool API 안 부름 (silent) |
| E2 | "아아 둘에 핫 아메리카노 하나" | 2 intent 확정 | "담을게요" 자연어만 | 동일 (parallel 명령도 무시) |
| D1 | "두유" missing | option_missing + 골조 명령 | "해당 옵션 없어요" (두유 단어 누락) | 골조 무시 |
| D2 | "오트밀크 라떼" missing | option_missing + 라떼 family 보조 | "어떤 라떼?" (오트 단어 누락) | 동일 |
| K1 | 복합 | 두 intent + missing 혼합 | 부분 응답 | 모델 처리 능력 한계 |

→ Phase 5 후속 검토: "NLU 모호 없는 의도 → 코드 직접 dispatch (모델 우회) → tool result → 모델 second-call에서 자연어 응답만". 모델 자유도 = 0. 큰 변화라 별도 plan 필요.

### 설계 회피 trigger 검토 (룰 6)

- proposal_renderer의 ⛔ 명령 = **결정론 강화** (모델 자유 영역 좁히기), 가드 아님 ✅
- change_option↔add_menu promotion = **NLU의 결정론적 분류** (같은 발화여도 카트 상태에 따라 의도 다름), 가드 아님 ✅
- C3 eval 정정 = **잘못된 측정 채점 교정**, NLU 모호 잡기 + 모델 되묻기는 정답 ✅

### Plan 종료

목표 80%+ 달성 (3회 모두 25/30=83%, 변동성 0). 137 unit tests pass. PR/commit 준비됨.
