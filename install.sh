#!/usr/bin/env bash
# ============================================================
# Best of the Market — One-shot installer
# Run on a fresh Ubuntu machine after extracting the zip:
#   cd best-of-the-market
#   chmod +x install.sh
#   ./install.sh
# ============================================================
set -e

echo "================================================"
echo "  Best of the Market — Installer"
echo "================================================"

# ---- Detect OS ----
. /etc/os-release
if [ "$ID" != "ubuntu" ] && [ "$ID" != "debian" ]; then
  echo "WARNING: This script is tested on Ubuntu/Debian. You may need to adapt the apt step."
fi

# ---- 1. System packages ----
echo ""
echo "[1/5] Installing system packages (python3-venv, python3-pip, nodejs, npm)..."
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq python3 python3-venv python3-pip nodejs npm curl >/dev/null
elif command -v dnf >/dev/null 2>&1; then
  sudo dnf install -y python3 python3-pip nodejs npm curl
else
  echo "ERROR: Could not find apt-get or dnf. Install Python 3.10+ and Node 18+ manually."
  exit 1
fi

# Check versions
PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
NODE_VERSION=$(node -v 2>/dev/null | tr -d 'v' || echo "0")
echo "  Python: $PYTHON_VERSION"
echo "  Node:   $NODE_VERSION"

if [ "$(printf '%s\n' "3.10" "$PYTHON_VERSION" | sort -V | head -n1)" != "3.10" ]; then
  echo "ERROR: Python 3.10 or higher required (found $PYTHON_VERSION)."
  exit 1
fi

# ---- 2. Python venv ----
echo ""
echo "[2/5] Creating Python virtual environment..."
cd "$(dirname "$0")"
python3 -m venv backend/venv
echo "  Created backend/venv"

# ---- 3. Python deps ----
echo ""
echo "[3/5] Installing Python dependencies (this may take a few minutes)..."
backend/venv/bin/pip install --upgrade pip --quiet
backend/venv/bin/pip install -r requirements.txt --quiet
echo "  Python packages installed."

# ---- 4. Node deps ----
echo ""
echo "[4/5] Installing Node dependencies (this may take a few minutes)..."
cd frontend
npm install --silent
cd ..
echo "  Node packages installed."

# ---- 5. Done ----
echo ""
echo "================================================"
echo "  ✅ Installation complete!"
echo "================================================"
echo ""
echo "To start the app, run:  ./run.sh"
echo "Then open:             http://localhost:5173/"
echo ""
