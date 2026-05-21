.PHONY: help install run test fmt up down restart logs status smoke clean

help: ## 도움말
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

install: ## venv 생성 + 의존성 설치
	python3.11 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt

run: ## host에서 직접 uvicorn 실행 (Ollama는 별도)
	.venv/bin/uvicorn app.api.app:app --host 0.0.0.0 --port 8080 --reload

test: ## unit + api 테스트 (ollama 마커 제외)
	.venv/bin/pytest app/tests/ -m "not ollama" -v

test-all: ## 전체 테스트 (ollama 라이브 필요)
	.venv/bin/pytest app/tests/ -v

up: ## docker compose 전체 기동
	docker compose up -d

down: ## docker compose 정지
	docker compose down

restart: ## 재시작
	docker compose restart

logs: ## 앱 로그
	docker compose logs -f app

logs-ollama: ## ollama 로그
	docker compose logs -f ollama

status: ## 컨테이너 상태
	docker compose ps

smoke: ## smoke test (compose up 후 1턴 curl)
	bash scripts/smoke_docker.sh

clean: ## 캐시 / pyc 정리
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
