.PHONY: help install run test test-all pull-model up up-bundled logs-ollama down restart logs status smoke clean

# .env가 있으면 읽어들이고, 없으면 아래 기본값 사용
-include .env
export
OLLAMA_MODEL ?= gemma4:e2b

help: ## 도움말
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

# --- 호스트 개발 (Docker 없이) ---
install: ## venv 생성 + 의존성 설치
	python3.11 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt

run: ## host에서 직접 uvicorn 실행 (Ollama는 별도로 떠 있어야 함)
	OLLAMA_BASE_URL=http://localhost:11434/v1 .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload

test: ## unit + api 테스트 (ollama 마커 제외)
	.venv/bin/pytest app/tests/ -m "not ollama" -v

test-all: ## 전체 테스트 (ollama 라이브 필요)
	.venv/bin/pytest app/tests/ -v

# --- 기본 구성: 앱만 Docker, Ollama는 호스트 (권장, macOS) ---
pull-model: ## 호스트 Ollama에 모델 받기 (OLLAMA_MODEL)
	ollama pull $(OLLAMA_MODEL)

up: ## 기본 기동 (앱만 Docker, 호스트 Ollama 사용)
	docker compose up -d

# --- 풀-Docker 구성: Ollama까지 컨테이너 (GPU 없는 Linux 등) ---
up-bundled: ## 풀-Docker 기동 (Ollama 컨테이너 포함)
	docker compose -f docker-compose.yml -f docker-compose.bundled.yml up -d

logs-ollama: ## ollama 로그 (풀-Docker 구성에서만)
	docker compose -f docker-compose.yml -f docker-compose.bundled.yml logs -f ollama

# --- 공통 ---
down: ## 컨테이너 정지 (두 구성 모두)
	docker compose -f docker-compose.yml -f docker-compose.bundled.yml down

restart: ## 앱 재시작
	docker compose restart

logs: ## 앱 로그
	docker compose logs -f app

status: ## 컨테이너 상태
	docker compose ps

smoke: ## smoke test (compose up 후 1턴 curl)
	bash scripts/smoke_docker.sh

clean: ## 캐시 / pyc 정리
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
