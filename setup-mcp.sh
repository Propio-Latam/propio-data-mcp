#!/usr/bin/env bash
# =============================================================================
# Propio Data MCP — One-command setup for Claude Code + Claude Desktop
#
# Usage:
#   curl -sL https://raw.githubusercontent.com/Propio-Latam/propio-data-mcp/main/setup-mcp.sh | bash
#
# Or download and run:
#   bash setup-mcp.sh
# =============================================================================
set -euo pipefail

API_KEY="f4e269254a2bfd08bab1852edf0e13b60b89fc1522649050"
MCP_URL="https://private-mcp.propio.cl/mcp/200502546258?token=${API_KEY}"

echo ""
echo "  Propio Data MCP — Setup"
echo "  ========================"
echo ""

# ---------- Check dependencies ----------

if ! command -v npx &>/dev/null; then
    echo "[!] Node.js is required but not installed."
    echo "    Install with: brew install node"
    echo ""
    exit 1
fi

if ! command -v python3 &>/dev/null; then
    echo "[!] Python3 is required but not installed."
    exit 1
fi

# ---------- Test connection ----------

echo "[*] Testing connection to MCP server..."
HEALTH=$(curl -sf https://private-mcp.propio.cl/health 2>/dev/null || echo "FAIL")
if [ "$HEALTH" = "FAIL" ]; then
    echo "[!] Cannot reach server at 34.61.255.37. Check your network."
    exit 1
fi
echo "[+] Server is up: $HEALTH"
echo ""

# ---------- Helper: merge mcpServers into a JSON config ----------

merge_config() {
    local FILE="$1"
    python3 << PYEOF
import json, os

file_path = "$FILE"
mcp_url = "$MCP_URL"

mcp_entry = {
    "command": "npx",
    "args": ["-y", "mcp-remote", mcp_url]
}

# Read existing config or start fresh
config = {}
if os.path.exists(file_path):
    try:
        with open(file_path) as f:
            config = json.load(f)
    except (json.JSONDecodeError, IOError):
        config = {}

# Merge
if "mcpServers" not in config:
    config["mcpServers"] = {}

if "creditu" in config["mcpServers"]:
    print(f"  [=] creditu already configured in {file_path}")
else:
    config["mcpServers"]["creditu"] = mcp_entry
    os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
    with open(file_path, "w") as f:
        json.dump(config, f, indent=2)
    print(f"  [+] Added creditu MCP to {file_path}")
PYEOF
}

# ---------- Claude Code ----------

echo "[*] Setting up Claude Code..."
CLAUDE_CODE_SETTINGS="$HOME/.claude/settings.json"
merge_config "$CLAUDE_CODE_SETTINGS"

# ---------- Claude Desktop ----------

echo "[*] Setting up Claude Desktop..."
if [ "$(uname)" = "Darwin" ]; then
    CLAUDE_DESKTOP_CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
else
    CLAUDE_DESKTOP_CONFIG="$HOME/.config/Claude/claude_desktop_config.json"
fi
merge_config "$CLAUDE_DESKTOP_CONFIG"

# ---------- Verify MCP auth ----------

echo ""
echo "[*] Verifying MCP authentication..."
AUTH_TEST=$(curl -sf -X POST "${MCP_URL}" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"setup-test","version":"0.1"}}}' 2>/dev/null || echo "FAIL")

if echo "$AUTH_TEST" | grep -q "serverInfo"; then
    echo "[+] MCP authenticated and responding!"
else
    echo "[!] MCP auth test failed. Response: $AUTH_TEST"
fi

# ---------- Done ----------

echo ""
echo "  Setup complete!"
echo "  ==============="
echo ""
echo "  Claude Code:    Restart Claude Code (exit and reopen)"
echo "  Claude Desktop: Quit (Cmd+Q) and reopen, then switch to Cowork mode"
echo ""
echo "  Try asking Claude:"
echo "    'List the tables in the creditu database'"
echo "    'Show me the top 10 debtors by total amount'"
echo ""
echo "  Server:   https://private-mcp.propio.cl"
echo "  API Docs: https://private-mcp.propio.cl/docs"
echo "  API Key:  $API_KEY"
echo ""
