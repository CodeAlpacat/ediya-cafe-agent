# Ediya Cafe Agent

로컬 Gemma 4 E2B(Ollama) 위에서 도는 이디야 커피 주문 챗봇. FastAPI + OpenAI Function Call. 한 화면에서 채팅하면 우측 카트가 실시간으로 갱신된다.

`docker compose up` 한 줄로 띄운다. 외부 API 키 필요 없다.

---

## 5초 데모

```bash
git clone git@github.com:CodeAlpacat/ediya-cafe-agent.git
cd ediya-cafe-agent
docker compose up -d
```

브라우저 → `http://localhost:8080`.

처음 한 번은 모델 pull 때문에 5~10분 걸린다. 이후엔 즉시 뜬다.

---

## 무엇을 하는가

- **채팅으로 주문**: "아이스 아메리카노 두 잔 주세요" → 카트에 즉시 반영.
- **자연어 옵션 변경**: "라떼로 바꿔주세요", "엑스트라 사이즈로" 등.
- **메뉴 추천**: "단 거 추천해주세요" → 임베딩 RAG가 후보 제시.
- **실시간 시각화**: 우측 패널이 카트 상태와 합계 가격을 라이브 표시.
- **대화 초기화**: 헤더 버튼 한 번에 세션·카트 리셋.

가짜 응답이 아니다. 모델이 실제로 OpenAI tool calling 프로토콜로 도구를 호출하고, 디스패처가 cart 상태를 바꾼다.

---

## 명령 (Makefile)

| 명령 | 동작 |
|------|------|
| `make up` | docker compose 전체 기동 (Ollama + 모델 pull + 앱) |
| `make down` | 컨테이너 정지 |
| `make logs` | 앱 로그 follow |
| `make logs-ollama` | Ollama 로그 follow |
| `make status` | 컨테이너 상태 |
| `make smoke` | up → curl 1턴 → assert. CI에서 사용 |
| `make install` | host venv 생성 (docker 안 쓸 때) |
| `make run` | host에서 uvicorn 실행. Ollama는 따로 띄워야 한다 |
| `make test` | 단위 + API 테스트 (ollama 마커 제외) |
| `make test-all` | 전체 (Ollama 실행 중이어야 함) |

---

## 구조

```
ediya-cafe-agent/
├── app/
│   ├── cart.py, menu.py, menu_data.yaml   # 도메인 — 52종 메뉴 + 6 옵션 카테고리
│   ├── tools.py                            # 7개 OpenAI Function Call 스키마
│   ├── prompts.py                          # 시스템 프롬프트 + few-shot
│   ├── dispatcher.py                       # tool_name → cart 메서드 + 도메인 검증
│   ├── agent.py                            # run_turn: LLM 멀티 라운드트립 + 5종 가드
│   ├── rag.py                              # 임베딩 기반 메뉴 검색 (ko-sroberta)
│   ├── api/                                # FastAPI 셸 + 세션 + 정적 데모
│   └── tests/                              # 87개 테스트 (unit + ollama 통합)
├── docs/design/                            # 아키텍처 / UI brief
├── scripts/                                # run_local.sh, smoke_docker.sh
├── Dockerfile
├── docker-compose.yml                      # ollama / ollama-init / app
├── Makefile
└── requirements.txt
```

---

## 아키텍처

```
브라우저 (좌 채팅 / 우 카트)
   │  HTTP
   ▼
FastAPI (app/api/app.py)
   │  /chat, /cart, /clear, /health
   ▼
SessionStore (in-memory, per-session lock)
   │
   ▼
agent.run_turn  ── LLM 호출 1 ──┐
   │                            │ tool_calls?
   │ ◄──── tool result ◄── dispatcher (Cart 변경 + 검증)
   │                            │
   └─── LLM 호출 2 (자연어 응답) ─┘
   │
   ▼
Ollama (gemma4:e2b) — OpenAI 호환 endpoint
```

LLM 라운드트립은 `agent.run_turn`이 다 처리한다. FastAPI는 얇은 어댑터다.

---

## E2B에서 작은 모델을 안정적으로 쓰기 위한 5가지 가드

작은 모델은 한국어 발화 라우팅이 흔들린다. 모든 가드는 **agent layer dynamic hint injection** 패턴으로 처리한다. 시스템 프롬프트 강화는 회귀 위험이 커서 안 쓴다.

| 코드 | 한계 | 처리 |
|------|------|------|
| P0 | 모델이 "담았어요" 응답하고 도구 안 부름 (silent corruption) | confirmation 동사 감지 + 1회 retry → fallback |
| P2 | 메뉴 hallucination ("단팥빙수 없어요" 거짓 거절) | RAG Step 1: 키워드 매칭으로 후보를 system hint로 주입 |
| P3 | 10턴+ 동일 tool_call 반복 (stuck loop) | (name+args) 3회 매칭 시 break + fallback 응답 |
| P4 | "아까 시킨 거" 같은 referential pronoun | cart 마지막 항목을 hint로 주입 |
| inquire→add 라우팅 | 메뉴 안내 받고 주문해도 inquiry 모드에 stuck | post-inquiry hint로 add_menu 강제 |

세 lever를 시험해본 결과:
- Tool description 변경 → whack-a-mole. 회귀 빈발.
- System prompt 강화 ("★ 최우선 규칙 ★") → 점수 떨어짐 (6/9 → 4/9).
- `tool_choice="required"` → Ollama가 무시.
- **agent layer dynamic hint injection** → 회귀 0, 정밀 처치.

자세한 설계 노트는 `docs/design/DESIGN.md`.

---

## 도메인 규칙

이디야 메뉴 규칙은 일반 카페와 다르다. 코드와 프롬프트에 박혀 있다.

- **사이즈**: `라지(L, 기본)` / `엑스트라(EX)`. 미명시 = L.
- **온도**: 아이스/핫은 옵션이 아니라 **메뉴 이름의 일부**. "아메리카노 주세요"는 모호 → 모델이 되묻는다.
- **디카페인**: 옵션 아니다. 별도 메뉴 카테고리. "디카페인 아메리카노" → `디카페인콜드브루아메리카노`로 매핑.
- **샷추가/시럽/휘핑/당도**: 커피/콜드브루 음료에 적용 가능한 옵션.

---

## 환경변수

```bash
cp .env.example .env
```

| 키 | 기본값 | 의미 |
|----|--------|------|
| `OLLAMA_BASE_URL` | `http://ollama:11434/v1` | OpenAI 호환 endpoint. host 직접 실행 시 `http://localhost:11434/v1` |
| `OLLAMA_MODEL` | `gemma4:e2b` | 사용할 Ollama 모델. ollama-init 컨테이너가 이 값으로 pull |

`.env`는 commit 금지. `.gitignore`에 박혀 있다.

---

## 트러블슈팅

**첫 `compose up`이 너무 오래 걸린다**
모델(~2GB) 다운로드 중이다. `make logs-ollama`로 진행 확인. 한 번 받으면 volume에 영속화된다.

**`localhost:11434` 포트 충돌**
host에서 이미 Ollama가 돌고 있을 가능성. `docker compose down` 후 host의 Ollama를 끄거나, `docker-compose.yml`에서 포트 매핑 제거.

**ARM Mac에서 응답이 느리다**
gemma4:e2b는 ARM에서 호환되지만 메모리 압박이 크다. 다른 무거운 앱 끄고 시도. 호스트에서 직접 Ollama 띄우는 게 빠를 수도 있다 (`make install && OLLAMA_BASE_URL=http://localhost:11434/v1 make run`).

**메모리 부족 (OOM)**
8GB RAM 권장. 임베딩 모델(ko-sroberta-multitask, ~400MB) + Ollama runtime + 모델까지 올라간다.

**테스트 일부 skip 됨**
`@pytest.mark.ollama` 마크된 테스트는 Ollama 라이브 + 모델 설치 필요. `.venv/bin/pytest app/tests/ -m "not ollama"`로 제외 가능.

---

## 한계

- **단일 사용자 데모**. 세션은 in-memory dict. 멀티 인스턴스 scale-out 안 된다. 필요하면 Redis로 교체.
- **6턴 이내 주문 흐름 권장**. 10턴 넘어가면 stuck loop 가드가 잡지만 사용자 경험이 떨어진다.
- **메뉴는 정확하게 말하는 게 안전**. "그거" "방금 그거"는 P4 가드가 처리하지만 100%는 아니다.
- **결제·POS 연동 없음**. cart는 메모리에만 남는다.

---

## 라이선스

MIT.

---

## 관련 문서

- `docs/design/DESIGN.md` — 아키텍처 + 도메인 모델 + 가드 학습 노트
- `docs/design/BRIEF_demo_ui.md` — UI shape brief
