#!/usr/bin/env bash
# Stops backend and frontend processes started by run.sh
cd "$(dirname "$0")"

stop_pid() {
  local pidfile="$1"
  if [ -f "$pidfile" ]; then
    local pid
    pid=$(cat "$pidfile")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      sleep 1
      kill -9 "$pid" 2>/dev/null || true
      echo "Stopped process $pid"
    fi
    rm -f "$pidfile"
  fi
}

stop_pid ".runtime/backend.pid"
stop_pid ".runtime/frontend.pid"

# Catch any stray processes too
pkill -f "uvicorn backend.app" 2>/dev/null || true
pkill -f "vite.*5173"           2>/dev/null || true

echo "All services stopped."
