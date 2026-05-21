# Design Brief: Ediya Cafe Agent — Web Demo UI

**작성일**: 2026-05-21
**대상**: `app/api/static/index.html` (FastAPI 정적 mount)

---

## 1. Feature Summary

이디야 커피 주문 챗봇과 대화하면서 **카트 상태가 우측 패널에 실시간으로 갱신되는 것을 보는** 단일 페이지 데모. 외부인이 "Gemma 4 E2B로 한국어 커피 주문이 진짜 되네"를 5분 안에 체감하는 게 목적.

## 2. Primary User Action

> "채팅에 주문 의도를 말한다 → 우측 카트 패널이 즉시 갱신된다."

이게 안 보이면 데모가 실패한다.

## 3. Design Direction

이디야 매장 키오스크 + 모던 챗 인터페이스의 교차점.
- **컬러**: 이디야 본사 진한 블루 `#003876`을 brand accent. 배경은 따뜻한 크림 `#F8F4ED`. surface는 화이트.
- **타이포**: system font (Pretendard/Noto Sans KR fallback), 헤더는 700, 본문 400.
- **분위기**: minimal + warm. 차가운 SaaS 톤은 피한다.
- **차별점**: 챗 UI에 가격 합계 라이브 표시 (이디야 영수증 풍).

## 4. Layout Strategy

```
┌──────────────────────────────────────────────────────┐
│ Header: 로고/타이틀 ──────────── [🧹 대화 초기화] │  56px
├────────────────────────┬─────────────────────────────┤
│                        │ 🛒 장바구니                  │
│  Chat scroll           │ ────────────                │
│  (assistant bubble L,  │  - 아이스아메리카노 × 1     │
│   user bubble R)       │    옵션: 라지                │
│                        │  - 카페모카 × 2             │
│  [typing indicator]    │ ────────────                │
│                        │ 합계: 9,500원                │
├────────────────────────┤                             │
│ Input bar:             │                             │
│ [텍스트 입력 ─────────] │                             │
│ [전송]                  │                             │
└────────────────────────┴─────────────────────────────┘
   ~60% width                  ~40% width
```

모바일 (`<768px`): 카트 패널이 채팅 위로 sticky하게 올라옴. 채팅이 메인, 카트는 collapsed accordion.

## 5. Key States

| State | 표시 |
|-------|------|
| **빈 카트** | "아직 담긴 메뉴가 없어요. 채팅으로 주문해 보세요." 큰 텍스트 + 작은 ☕ 아이콘 |
| **카트 항목 있음** | 각 라인 = 메뉴명(굵게) + 수량 chip + 옵션 작은 텍스트 + 단가. 하단 합계 강조 |
| **로딩** | 채팅 영역 마지막에 "·  ·  ·" 점 3개 애니메이션 (1초 cycle) |
| **에러** | 우측 상단 작은 빨간 토스트 (`fixed top-right`, 4초 후 자동 사라짐) |
| **세션 새로 시작** | localStorage에 session_id 저장, 페이지 새로고침해도 유지 |

## 6. Interaction Model

- **메시지 전송**: Enter (Shift+Enter 줄바꿈), [전송] 버튼.
- **응답 도착**: 마지막 메시지 위로 부드럽게 스크롤. 카트 패널은 응답의 `cart` 필드로 immediate 교체 (diff 애니메이션 X — 단순 replace가 더 빠름).
- **초기화 버튼**: 헤더 우측. 클릭 시 `POST /clear` + UI 비움. 확인 모달 X (데모니까 가벼움 우선).
- **에러 핸들링**: 네트워크 실패 / 500 → 토스트. 채팅 메시지는 사용자가 보낸 그대로 유지 (재전송 가능하게).

## 7. Content Requirements

- 헤더 타이틀: "이디야 커피 ☕"
- 부제 (작게): "Gemma 4 E2B 로컬 데모"
- 빈 입력 placeholder: "어떤 음료를 드릴까요? (예: 아이스 아메리카노 한 잔)"
- 빈 카트: "아직 담긴 메뉴가 없어요"
- 초기화 버튼: "🧹 대화 초기화"
- 합계 prefix: "합계"
- 에러 토스트: "서버와 연결이 끊겼어요. 다시 시도해 주세요."

## 8. Implementation Constraints

- **No framework**. Vanilla HTML/CSS/JS. CDN 의존성 X (오프라인 docker에서도 동작).
- 한 파일에 다 넣지 말고 `index.html` / `app.js` / `style.css` 분리.
- 전체 합쳐서 ~500줄 이내 목표.
- 가격은 클라이언트 사이드에서 메뉴 lookup 없이 — **응답의 cart 필드에 가격 포함 안 됨** → menu_data.yaml을 직접 import 못 함 → `/menu/prices` 같은 endpoint 추가 또는 응답에 가격 포함.
  - 결정: **응답 `cart` 항목에 가격 포함하도록 백엔드 수정**. snapshot()에 price_l 추가.

## 9. Open Questions Resolved

- Q: 가격을 클라이언트에서 계산? → A: 백엔드 snapshot에 price 포함 (Phase 2 Task 추가).
- Q: 옵션 표시 방식? → A: 메뉴명 옆 작은 텍스트로 콤마 나열. (예: "옵션: 라지, 샷추가")
- Q: 카트 항목 클릭 액션? → A: v1은 read-only. 수정은 채팅으로만.
