#!/usr/bin/env bash
# One-command launch: MongoDB -> API (uvicorn) -> desktop app (PySide6).
# Ctrl+C stops the desktop app and the API; MongoDB keeps running
# (stop it with: make down).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f backend/.env ]; then
  echo "backend/.env not found — run: make setup" >&2
  exit 1
fi
if [ ! -x backend/.venv/bin/uvicorn ] || [ ! -x frontend/.venv/bin/python ]; then
  echo "virtualenvs missing or incomplete — run: make setup" >&2
  exit 1
fi

echo "==> MongoDB"
docker compose -f backend/docker-compose.yml up -d
echo -n "    waiting for healthy"
for _ in $(seq 1 30); do
  status=$(docker inspect --format '{{.State.Health.Status}}' data-crawler-mongo 2>/dev/null || echo "missing")
  [ "$status" = "healthy" ] && break
  echo -n "."
  sleep 1
done
echo " ${status:-missing}"
if [ "${status:-}" != "healthy" ]; then
  echo "MongoDB did not become healthy — check: docker compose -f backend/docker-compose.yml logs" >&2
  exit 1
fi

echo "==> API on :8000 (log: .backend.log)"
(cd backend && exec .venv/bin/uvicorn app.main:app --port 8000) > .backend.log 2>&1 &
BACKEND_PID=$!
echo "$BACKEND_PID" > .backend.pid
cleanup() {
  kill "$BACKEND_PID" 2>/dev/null || true
  rm -f .backend.pid
}
trap cleanup EXIT

echo -n "    waiting for /api/health"
for _ in $(seq 1 30); do
  if curl -sf http://localhost:8000/api/health > /dev/null 2>&1; then
    echo " ok"
    break
  fi
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo " API died — last log lines:" >&2
    tail -20 .backend.log >&2 || true
    exit 1
  fi
  echo -n "."
  sleep 1
done
if ! curl -sf http://localhost:8000/api/health > /dev/null 2>&1; then
  echo " API did not become ready — check .backend.log" >&2
  exit 1
fi

echo "==> desktop app (Ctrl+C stops app + API)"
cd frontend && .venv/bin/python main.py
