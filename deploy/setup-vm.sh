#!/usr/bin/env bash
# =============================================================================
# MCP Data Bridge — VM initial setup (run once on a fresh e2-micro)
# Usage: ssh into the VM, then: curl -sL <raw-github-url>/deploy/setup-vm.sh | bash
# =============================================================================
set -euo pipefail

echo ">>> Updating system packages"
sudo apt-get update -qq && sudo apt-get upgrade -y -qq

echo ">>> Installing PostgreSQL 16"
sudo apt-get install -y -qq postgresql postgresql-contrib

echo ">>> Installing Python 3.11+ and pip"
sudo apt-get install -y -qq python3 python3-pip python3-venv git nginx certbot python3-certbot-nginx

echo ">>> Creating app user"
sudo useradd -r -m -s /bin/bash mcpbridge || true

echo ">>> Creating app directory"
sudo mkdir -p /opt/mcp-data-bridge
sudo chown mcpbridge:mcpbridge /opt/mcp-data-bridge

echo ">>> Setting up swap (2 GB)"
if [ ! -f /swapfile ]; then
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    echo "Swap enabled"
else
    echo "Swap already exists"
fi

echo ">>> Configuring PostgreSQL"
sudo -u postgres psql -c "CREATE USER mcpbridge WITH PASSWORD 'mcpbridge';" 2>/dev/null || true
sudo -u postgres psql -c "ALTER USER mcpbridge CREATEDB;" 2>/dev/null || true

# Allow local connections
sudo sed -i 's/^#listen_addresses.*/listen_addresses = '\''localhost'\''/' /etc/postgresql/*/main/postgresql.conf
echo "shared_buffers = 128MB" | sudo tee -a /etc/postgresql/*/main/conf.d/custom.conf
echo "work_mem = 4MB" | sudo tee -a /etc/postgresql/*/main/conf.d/custom.conf
sudo systemctl restart postgresql

echo ">>> Installing systemd service"
sudo tee /etc/systemd/system/mcp-data-bridge.service > /dev/null << 'UNIT'
[Unit]
Description=MCP Data Bridge
After=network.target postgresql.service
Requires=postgresql.service

[Service]
Type=simple
User=mcpbridge
Group=mcpbridge
WorkingDirectory=/opt/mcp-data-bridge
EnvironmentFile=/opt/mcp-data-bridge/.env
ExecStart=/opt/mcp-data-bridge/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable mcp-data-bridge

echo ">>> Setting up nginx reverse proxy"
sudo tee /etc/nginx/sites-available/mcp-data-bridge > /dev/null << 'NGINX'
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE support — disable buffering
        proxy_buffering off;
        proxy_cache off;
        proxy_set_header Connection '';
        chunked_transfer_encoding off;

        # Long timeout for SSE connections
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }
}
NGINX

sudo ln -sf /etc/nginx/sites-available/mcp-data-bridge /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx

echo ""
echo "========================================="
echo "  VM setup complete!"
echo "  Next: run deploy/deploy.sh to push code"
echo "========================================="
