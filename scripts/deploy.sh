#!/usr/bin/env bash
set -euo pipefail

# One-command VPS deploy script for Jarvis OS
# Usage: ./scripts/deploy.sh [--build-ui]

APP_DIR="${JARVIS_APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SERVICE_NAME="${JARVIS_SERVICE:-jarvis}"
HEALTH_URL="${JARVIS_HEALTH_URL:-http://127.0.0.1:7777/api/health}"
BUILD_UI=false

for arg in "$@"; do
  case "$arg" in
    --build-ui) BUILD_UI=true ;;
  esac
done

echo "==> Deploying Jarvis OS in ${APP_DIR}"
cd "${APP_DIR}"

echo "==> Pulling latest main..."
git fetch origin main
git pull --ff-only origin main

if [ -f "backend/requirements.txt" ] && [ -x ".venv/bin/pip" ]; then
  echo "==> Checking Python dependencies..."
  .venv/bin/pip install -q -r backend/requirements.txt
fi

if [ "${BUILD_UI}" = true ] && [ -d "dashboard" ] && command -v npm >/dev/null 2>&1; then
  echo "==> Building dashboard..."
  (cd dashboard && npm ci --no-audit --no-fund && npm run build)
fi

if command -v systemctl >/dev/null 2>&1; then
  echo "==> Restarting systemd service '${SERVICE_NAME}'..."
  sudo systemctl restart "${SERVICE_NAME}"
  sudo systemctl is-active --quiet "${SERVICE_NAME}"
else
  echo "==> systemctl not found; skipping service restart."
fi

echo "==> Waiting for healthcheck at ${HEALTH_URL}..."
for i in $(seq 1 20); do
  if curl -fsS "${HEALTH_URL}" >/dev/null 2>&1; then
    echo "==> Healthcheck OK!"
    curl -fsS "${HEALTH_URL}"
    echo ""
    exit 0
  fi
  sleep 1
done

echo "ERROR: Healthcheck failed at ${HEALTH_URL}" >&2
exit 1
