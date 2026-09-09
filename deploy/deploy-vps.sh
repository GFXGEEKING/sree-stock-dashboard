#!/usr/bin/env bash
# ============================================================
# Sree Stock Dashboard — VPS deploy/update (run ON THE VPS)
# Idempotent: first run provisions, later runs update code+build.
# Usage:  bash /opt/sree-stocks/deploy/deploy-vps.sh
# ============================================================
set -euo pipefail

APP_DIR="/opt/sree-stocks"
VENV="$APP_DIR/backend/venv"
DOMAIN="sreestocktrading.duckdns.org"

echo "==> Code checkout"
mkdir -p "$APP_DIR"
if [ ! -d "$APP_DIR/.git" ]; then
  git clone /srv/git/sree-stocks.git "$APP_DIR"
else
  git -C "$APP_DIR" pull --ff-only origin main || git -C "$APP_DIR" fetch origin
  git -C "$APP_DIR" reset --hard origin/main
fi

echo "==> Backend venv + dependencies"
if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

mkdir -p "$APP_DIR/backend/data"

echo "==> Frontend build"
if ! command -v node >/dev/null 2>&1; then
  echo "Node not found — install Node 22 LTS via NodeSource first" >&2
  exit 1
fi
cd "$APP_DIR/frontend"
npm ci --silent 2>/dev/null || npm install --silent
npm run build

echo "==> systemd service"
systemctl daemon-reload
systemctl enable sree-stocks.service >/dev/null 2>&1 || true
systemctl restart sree-stocks.service

echo "==> nginx site"
cp "$APP_DIR/deploy/nginx-sree-stocks.conf" "/etc/nginx/sites-available/sree-stocks"
ln -sf "/etc/nginx/sites-available/sree-stocks" "/etc/nginx/sites-enabled/sree-stocks"
nginx -t
systemctl reload nginx

echo "==> Done. Backend: systemctl status sree-stocks — https://$DOMAIN"
