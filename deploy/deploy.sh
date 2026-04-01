#!/usr/bin/env bash
# =============================================================================
# MCP Data Bridge — Deploy to VM (run on the VM, or called by GitHub Actions)
# =============================================================================
set -euo pipefail

APP_DIR="/opt/mcp-data-bridge"

echo ">>> Pulling latest code"
cd "$APP_DIR"
git fetch origin main
git reset --hard origin/main

echo ">>> Installing/updating Python dependencies"
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

echo ">>> Creating .env if missing"
if [ ! -f .env ]; then
    cp .env.example .env
    echo "WARNING: Created .env from example — edit it with real values!"
fi

echo ">>> Restarting service"
sudo systemctl restart mcp-data-bridge

echo ">>> Checking service status"
sleep 2
if sudo systemctl is-active --quiet mcp-data-bridge; then
    echo "Service is running"
    curl -s http://127.0.0.1:8000/health
    echo ""
else
    echo "ERROR: Service failed to start"
    sudo journalctl -u mcp-data-bridge --no-pager -n 20
    exit 1
fi

echo ""
echo ">>> Deploy complete!"
