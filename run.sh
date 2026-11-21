#!/usr/bin/env bash
# Jarvis OS - One-command local startup script
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "============================================================"
echo " Starting Jarvis OS (Personal Agentic Operating System)"
echo "============================================================"

# 1. Virtual Environment Setup
if [ ! -d ".venv" ]; then
    echo "[-] Creating virtual environment (.venv)..."
    python3 -m venv .venv
fi

source .venv/bin/activate

# 2. Dependency Check & Installation
echo "[-] Checking backend Python dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r backend/requirements.txt

# 3. Frontend Dashboard Build Check
if [ ! -d "dashboard/dist" ]; then
    echo "[-] Dashboard dist not found. Building web frontend..."
    if command -v npm >/dev/null 2>&1; then
        (cd dashboard && npm install --silent && npm run build)
    else
        echo "[!] Warning: npm not found in PATH. Pre-built dashboard might be missing."
    fi
fi

# 4. Environment Check
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    echo "[-] Creating .env from .env.example..."
    cp .env.example .env
fi

# 5. Launch Server
PORT="${JARVIS_PORT:-7777}"
HOST="${JARVIS_HOST:-0.0.0.0}"
export JARVIS_ALLOWED_HOSTS="${JARVIS_ALLOWED_HOSTS:-*}"

echo ""
echo "Jarvis OS is ready!"
echo "Dashboard Web UI:   http://${HOST}:${PORT}"
echo "Cursor / IDE MCP:   http://${HOST}:${PORT}/hub/mcp"
echo "Healthcheck API:    http://${HOST}:${PORT}/api/health"
echo "Press CTRL+C to stop the server."
echo "============================================================"
echo ""

exec uvicorn app.main:app --app-dir backend --host "$HOST" --port "$PORT"
