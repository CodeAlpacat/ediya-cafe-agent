# Ediya Cafe Agent

로컬 Gemma 4 E2B(Ollama) 위에서 도는 이디야 커피 주문 챗봇. FastAPI + OpenAI Function Call. 한 화면에서 채팅하면 우측 카트가 실시간으로 갱신된다.

앱은 Docker로, LLM(Ollama)은 호스트에서 직접 돌린다. 외부 API 키는 필요 없다.

---

## 실행 방식

macOS의 Docker는 리눅스 VM 안에서 돌기 때문에 Apple GPU(Metal)에 접근하지 못한다.
Ollama를 Docker 안에 두면 CPU 추론만 가능해 LLM이 사실상 못 쓸 만큼 느리다.
그래서 기본 구성은 **앱만 Docker, Ollama는 호스트 네이티브**다.

기본 모델은 `gemma4:e2b`(~7GB)다. 16GB RAM 머신에서는 GPU 메모리 확보를 위해 여유 RAM이
넉넉해야 한다(트러블슈팅 참고). `OLLAMA_MODEL` 환경변수로 교체할 수 있다.

### A. 기본 구성 — 호스트 Ollama (권장, macOS)

사전 요구: 호스트에 [Ollama](https://ollama.com/download) 설치.

```bash
# 1) Ollama 실행 + 모델 받기 (최초 1회, ~7GB)
ollama serve              # 또는 Ollama.app 실행
ollama pull gemma4:e2b    # = make pull-model

# 2) 앱 기동
git clone git@github.com:CodeAlpacat/ediya-cafe-agent.git
cd ediya-cafe-agent
docker compose up -d      # = make up
```

브라우저 → `http://localhost:8080`.

앱 컨테이너는 `host.docker.internal:11434`로 호스트 Ollama에 접속한다.

### B. 풀-Docker 구성 — Ollama까지 컨테이너 (GPU 없는 Linux 서버 등)

```bash
docker compose -f docker-compose.yml -f docker-compose.bundled.yml up -d
```

`ollama-init` 컨테이너가 모델을 자동으로 pull 한다(최초 5~10분, volume에 영속화).
GPU 가속이 없어 CPU 추론이며 macOS에서는 매우 느리다 — Linux/GPU 환경에서만 권장.

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
| `make pull-model` | 호스트 Ollama에 모델 받기 (`OLLAMA_MODEL`) |
| `make up` | 기본 구성 기동 (앱만 Docker, 호스트 Ollama 사용) |
| `make up-bundled` | 풀-Docker 기동 (Ollama 컨테이너 + 모델 pull + 앱) |
| `make down` | 컨테이너 정지 (두 구성 모두) |
| `make logs` | 앱 로그 follow |
| `make logs-ollama` | Ollama 로그 follow (풀-Docker 구성에서만) |
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
│   ├── main.py                             # FastAPI 팩토리 + lifespan (entrypoint: app.main:app)
│   ├── core/                               # 교차 관심사
│   │   ├── config.py                       #   Settings(BaseSettings) — env/.env 일원화
│   │   └── logging.py                      #   setup_logging — lifespan startup에서 호출
│   ├── api/                                # HTTP 경계
│   │   ├── schemas.py                      #   요청/응답 Pydantic 모델
│   │   ├── deps.py                         #   Depends 프로바이더 (settings/store/client)
│   │   ├── middleware.py                   #   request/response 로깅
│   │   └── routers/                        #   엔드포인트별 라우터
│   │       ├── chat.py                     #     POST /chat
│   │       ├── cart.py                     #     GET /cart, POST /clear
│   │       ├── health.py                   #     GET /health
│   │       └── static.py                   #     / + /static mount
│   ├── services/                           # use-case 레이어 (HTTP 무관)
│   │   ├── chat_service.py                 #   process_message (asyncio.to_thread wrap)
│   │   ├── cart_pricing.py                 #   enrich + total 계산
│   │   └── session_store.py                #   in-memory 세션 + 정기 정리
│   ├── llm/                                # LLM 레이어
│   │   ├── client.py                       #   OpenAI 호환 클라이언트 팩토리
│   │   ├── prompts.py                      #   활성 카페의 system_prompt.txt 로드
│   │   ├── tools.py                        #   7개 OpenAI Function Call 스키마
│   │   ├── rag.py                          #   임베딩 기반 메뉴 검색 (ko-sroberta)
│   │   └── agent/                          #   run_turn + 가드/힌트 패키지
│   │       ├── runner.py                   #     run_turn 본 루프 + AgentConfig
│   │       ├── guards.py                   #     P0/P3 가드 + silent-add 복구
│   │       └── hints.py                    #     발화 분석 + system hint 생성
│   ├── dispatcher.py                       # tool_name → cart 메서드 + 도메인 검증
│   ├── domain/                             # 도메인 레이어 — LLM/웹 비의존
│   │   ├── cart.py                         #   카트 상태 (검증은 dispatcher가)
│   │   └── menu.py                         #   메뉴 로더 + 검증 + 키워드 검색
│   ├── cafe_profile.py                     # 활성 카페(CAFE_PROFILE) 로더
│   ├── cafes/                              # 카페별 콘텐츠 — 교체 단위
│   │   └── ediya/                          #   CAFE_PROFILE=ediya
│   │       ├── menu_data.yaml              #   52종 메뉴 + 카테고리 + 옵션
│   │       ├── system_prompt.txt           #   시스템 프롬프트 전문 + few-shot
│   │       └── profile.yaml                #   카페명 + 줄임말 맵
│   └── tests/                              # 테스트 (unit + ollama 통합)
├── docs/design/                            # 아키텍처 / UI brief
├── scripts/                                # run_local.sh, smoke_docker.sh
├── Dockerfile
├── docker-compose.yml                      # app만 — 호스트 Ollama 사용 (기본)
├── docker-compose.bundled.yml              # + ollama/ollama-init 오버라이드 (풀-Docker)
├── Makefile
└── requirements.txt
```

---

## 아키텍처

```
브라우저 (좌 채팅 / 우 카트)
   │  HTTP
   ▼
FastAPI (app/main.py + app/api/routers/*)
   │  /chat, /cart, /clear, /health
   ▼
SessionStore (in-memory, per-session lock)
   │
   ▼
llm.agent.run_turn ── LLM 호출 1 ──┐
   │                            │ tool_calls?
   │ ◄──── tool result ◄── dispatcher (Cart 변경 + 검증)
   │                            │
   └─── LLM 호출 2 (자연어 응답) ─┘
   │
   ▼
Ollama (gemma4:e2b) — 호스트 네이티브, OpenAI 호환 endpoint
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
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434/v1` | OpenAI 호환 endpoint. 기본은 호스트 Ollama. 풀-Docker 구성은 자동으로 `http://ollama:11434/v1`로 덮어쓴다 |
| `OLLAMA_MODEL` | `gemma4:e2b` | 사용할 Ollama 모델. 풀-Docker 구성에서 ollama-init 컨테이너가 이 값으로 pull |
| `CAFE_PROFILE` | `ediya` | 활성 카페. `app/cafes/<이름>/` 폴더 이름과 일치 |

`.env`는 commit 금지. `.gitignore`에 박혀 있다.

### 카페 교체

메뉴·도메인 규칙·줄임말은 코드가 아니라 `app/cafes/<카페>/` 폴더에 있다. 다른 카페를 쓰려면:

1. `app/cafes/<새카페>/` 폴더를 만들고 세 파일을 둔다 — `menu_data.yaml`(메뉴/옵션/카테고리), `system_prompt.txt`(점원 페르소나 + 도메인 규칙 + few-shot), `profile.yaml`(카페명 + 줄임말 맵).
2. `CAFE_PROFILE=<새카페>` 로 지정.

RAG 인덱스는 카페별로 분리 캐시되고, `menu_data.yaml` 변경 시 자동 재구축된다.

---

## 트러블슈팅

**앱이 LLM에 연결을 못 한다 (기본 구성)**
호스트 Ollama가 떠 있는지 확인: `curl http://localhost:11434/api/version`.
안 뜨면 `ollama serve` 또는 Ollama.app 실행. 모델이 없으면 `ollama pull gemma4:e2b`.

**응답이 극단적으로 느리다 / 모델 로딩이 안 끝난다**
Ollama를 Docker 안에서 돌리고 있을 가능성. macOS Docker는 GPU를 못 써서 CPU 추론만 되고, 짧은 응답에도 수 분이 걸린다. 기본 구성(호스트 Ollama)으로 전환할 것. `ollama ps`로 `100% GPU`인지 확인.

**`localhost:11434` 포트 충돌**
호스트 Ollama와 풀-Docker의 ollama 컨테이너가 둘 다 11434를 잡으려 할 때. 기본 구성에서는 호스트 Ollama만, 풀-Docker 구성에서는 컨테이너만 쓰도록 한쪽을 정리.

**첫 모델 pull이 오래 걸린다**
`gemma4:e2b`는 ~7GB다. 호스트는 `ollama pull` 진행률이 바로 보이고, 풀-Docker는 `make logs-ollama`로 확인.

**메모리 부족 / GPU 대신 CPU로 떨어진다**
임베딩 모델(ko-sroberta-multitask, ~400MB) + Ollama + gemma4:e2b(~7GB)가 동시에 올라간다. 호스트 RAM 16GB 권장.
Apple Silicon은 통합 메모리라 여유 RAM이 부족하면 Ollama가 GPU 대신 CPU로 떨어진다(`ollama ps`로 `100% GPU` 확인). 16GB 머신에서는 무거운 앱을 닫거나 재부팅으로 여유 메모리를 확보한 뒤 모델을 로드할 것.

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
