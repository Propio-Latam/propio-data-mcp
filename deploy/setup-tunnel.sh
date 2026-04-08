#!/usr/bin/env bash
# =============================================================================
# Cloudflare Tunnel Setup for MCP Data Bridge
#
# Prerequisites:
#   1. Create a tunnel in Cloudflare Zero Trust dashboard:
#      Zero Trust → Networks → Tunnels → Create a tunnel
#      Name: mcp-data-bridge
#      Copy the tunnel token.
#
#   2. Configure tunnel routing in dashboard:
#      Public hostname: private-mcp.propio.cl
#      Service: http://127.0.0.1:80
#
#   3. Set up Cloudflare Access (Zero Trust → Access → Applications):
#      - Type: Self-hosted
#      - App name: "MCP Data Bridge Portal"
#      - Domain: private-mcp.propio.cl
#      - Path: /portal/*
#      - Policy: Allow → Emails: enzo@propiolatam.com, francisco@propiolatam.com
#      - Session duration: 24 hours
#      - IMPORTANT: Do NOT protect /mcp/* or /api/* (they use API key auth)
#
# Usage:
#   bash deploy/setup-tunnel.sh <TUNNEL_TOKEN>
# =============================================================================
set -euo pipefail

TUNNEL_TOKEN="${1:-}"

if [ -z "$TUNNEL_TOKEN" ]; then
    echo "Usage: bash deploy/setup-tunnel.sh <TUNNEL_TOKEN>"
    echo ""
    echo "Get your tunnel token from:"
    echo "  Cloudflare Zero Trust → Networks → Tunnels → mcp-data-bridge → Configure"
    exit 1
fi

echo ">>> Installing cloudflared"
if ! command -v cloudflared &>/dev/null; then
    curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o /tmp/cloudflared.deb
    sudo dpkg -i /tmp/cloudflared.deb
    rm /tmp/cloudflared.deb
else
    echo "    cloudflared already installed: $(cloudflared --version)"
fi

echo ">>> Installing cloudflared as a service"
sudo cloudflared service install "$TUNNEL_TOKEN"

echo ">>> Starting cloudflared service"
sudo systemctl enable cloudflared
sudo systemctl start cloudflared

echo ">>> Verifying cloudflared is running"
sleep 3
if sudo systemctl is-active --quiet cloudflared; then
    echo "    cloudflared is running"
else
    echo "    ERROR: cloudflared failed to start"
    sudo journalctl -u cloudflared --no-pager -n 10
    exit 1
fi

echo ">>> Updating nginx to listen on localhost only"
sudo sed -i 's/listen 80;/listen 127.0.0.1:80;/' /etc/nginx/sites-available/mcp-data-bridge
sudo sed -i 's/listen \[::\]:80;/# listen [::]:80;/' /etc/nginx/sites-available/mcp-data-bridge

echo ">>> Adding upload size limit to nginx"
if ! grep -q "client_max_body_size" /etc/nginx/sites-available/mcp-data-bridge; then
    sudo sed -i '/server_name/a\    client_max_body_size 100M;' /etc/nginx/sites-available/mcp-data-bridge
fi

sudo nginx -t && sudo systemctl restart nginx

echo ">>> Updating .env for production"
if grep -q "^ENVIRONMENT=" /opt/mcp-data-bridge/.env; then
    sudo sed -i 's/^ENVIRONMENT=.*/ENVIRONMENT=production/' /opt/mcp-data-bridge/.env
else
    echo "ENVIRONMENT=production" | sudo tee -a /opt/mcp-data-bridge/.env
fi

echo ""
echo "========================================="
echo "  Tunnel setup complete!"
echo "========================================="
echo ""
echo "  Next steps:"
echo "  1. In GCE Console: remove firewall rule 'allow tcp:80' for this VM"
echo "     (traffic now goes through Cloudflare Tunnel only)"
echo ""
echo "  2. Verify: https://private-mcp.propio.cl/health"
echo "  3. Verify portal: https://private-mcp.propio.cl/portal/"
echo "  4. Verify MCP still works: https://private-mcp.propio.cl/mcp/200502546258"
echo ""
echo "  If anything breaks, restore with:"
echo "    sudo systemctl stop cloudflared"
echo "    sudo sed -i 's/listen 127.0.0.1:80;/listen 80;/' /etc/nginx/sites-available/mcp-data-bridge"
echo "    sudo systemctl restart nginx"
echo "    # Re-add GCE firewall rule for tcp:80"
echo ""
