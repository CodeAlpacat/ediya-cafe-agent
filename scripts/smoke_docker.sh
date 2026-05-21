#!/usr/bin/env bash
# docker compose 전체 기동 + /chat 1턴 검증.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "[smoke] compose up..."
docker compose up -d

echo "[smoke] app healthcheck 대기..."
for i in $(seq 1 30); do
  if curl -fsS http://localhost:8080/health >/dev/null 2>&1; then
    echo "[smoke] app ready"
    break
  fi
  echo "  ($i) not ready yet, waiting 5s..."
  sleep 5
done

echo "[smoke] POST /chat..."
RESP=$(curl -sf -X POST http://localhost:8080/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"smoke","message":"아이스 아메리카노 한 잔"}' \
  --max-time 90)

echo "[smoke] response: $RESP"

if ! echo "$RESP" | grep -q "session_id"; then
  echo "[smoke] FAIL — response invalid"
  exit 1
fi

echo "[smoke] OK"
