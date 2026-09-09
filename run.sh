#!/usr/bin/env bash
# ============================================================
# Best of the Market — Launcher
# Starts backend (port 8000) and frontend (port 5173) in background.
# Stop with:  ./stop.sh
# Logs:       backend.log  and  frontend.log
# ============================================================
set -e

cd "$(dirname "$0")"
mkdir -p .runtime

# --- Stop any previous instances ---
./stop.sh >/dev/null 2>&1 || true

# --- Backend ---
echo "Starting backend on http://localhost:8000 ..."
nohup backend/venv/bin/python -m uvicorn backend.app:app \
  --host 0.0.0.0 --port 8000 \
  > .runtime/backend.log 2>&1 &
echo $! > .runtime/backend.pid

# --- Frontend ---
echo "Starting frontend on http://localhost:5173 ..."
cd frontend
nohup npm run dev -- --host 0.0.0.0 --port 5173 \
  > ../.runtime/frontend.log 2>&1 &
echo $! > ../.runtime/frontend.pid
cd ..

# --- Wait for both to be ready ---
echo "Waiting for services to be ready..."
for i in {1..30}; do
  sleep 1
  BACKEND_OK=$(curl -sf -m 2 http://localhost:8000/api/health >/dev/null 2>&1 && echo y || echo n)
  FRONTEND_OK=$(curl -sf -m 2 http://localhost:5173/ >/dev/null 2>&1 && echo y || echo n)
  if [ "$BACKEND_OK" = "y" ] && [ "$FRONTEND_OK" = "y" ]; then
    echo ""
    echo "================================================"
    echo "  ✅ Both services are up!"
    echo "================================================"
    echo "  Dashboard:  http://localhost:5173/"
    echo "  API:        http://localhost:8000/"
    echo "  API docs:   http://localhost:8000/docs"
    echo ""
    echo "  Logs:  tail -f .runtime/backend.log .runtime/frontend.log"
    echo "  Stop:  ./stop.sh"
    echo "================================================"
    exit 0
  fi
done

echo ""
echo "WARNING: services did not respond within 30s. Check logs:"
echo "  tail -f .runtime/backend.log"
echo "  tail -f .runtime/frontend.log"
