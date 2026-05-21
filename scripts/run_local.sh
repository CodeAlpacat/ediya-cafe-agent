#!/usr/bin/env bash
# host에서 venv로 직접 실행. Ollama는 별도로 띄워둔 상태 가정.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  echo "[run_local] .venv 없음 — make install 먼저"
  exit 1
fi

export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434/v1}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-gemma4:e2b}"

exec .venv/bin/uvicorn app.api.app:app --host 0.0.0.0 --port 8080 --reload
