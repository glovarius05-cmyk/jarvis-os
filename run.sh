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

VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python3"

# 2. Dependency Check & Installation
echo "[-] Checking backend Python dependencies..."
"$VENV_PYTHON" -m pip install --quiet --upgrade pip
"$VENV_PYTHON" -m pip install --quiet -r backend/requirements.txt

# 3. Frontend Dashboard Build Check
if [ ! -d "dashboard/dist" ]; then
    echo "[-] Dashboard dist not found. Building web frontend..."
    export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
    if command -v npm >/dev/null 2>&1; then
        (cd dashboard && npm install --silent && npm run build)
    else
        echo "[!] Warning: npm not found in PATH. Pre-built dashboard might be missing."
    fi
fi

# 4. Environment Check & Loading
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    echo "[-] Creating .env from .env.example..."
    cp .env.example .env
fi

if [ -f ".env" ]; then
    set -a
    # shellcheck disable=SC1091
    source ".env"
    set +a
fi

# 5. Launch Server
PORT="${JARVIS_PORT:-7777}"
HOST="${JARVIS_HOST:-127.0.0.1}"
export JARVIS_HOST="$HOST"
export JARVIS_PORT="$PORT"

echo ""
echo "Jarvis OS is ready!"
echo "Dashboard Web UI:   http://${HOST}:${PORT}"
echo "Cursor / IDE MCP:   http://${HOST}:${PORT}/hub/mcp"
echo "Healthcheck API:    http://${HOST}:${PORT}/api/health"
echo "Press CTRL+C to stop the server."
echo "============================================================"
echo ""

exec "$VENV_PYTHON" -m uvicorn app.main:app --app-dir backend --host "$HOST" --port "$PORT"
