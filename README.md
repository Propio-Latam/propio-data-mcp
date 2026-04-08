# Private MCP — Propio Latam

A secure platform that turns Excel files into AI-queryable databases. Upload spreadsheets through a web portal, and Claude can instantly query the data via [Model Context Protocol (MCP)](https://modelcontextprotocol.io).

**Live:** [private-mcp.propiolatam.com](https://private-mcp.propiolatam.com/portal/)

## How It Works

```
Excel file → Upload Portal → PostgreSQL → MCP Server → Claude
```

1. A team member uploads `.xlsx` files through the web portal
2. The data is loaded into a dedicated PostgreSQL database
3. An MCP endpoint is automatically created for that database
4. Claude (Desktop or Code) can query it with SQL — list tables, describe schemas, run queries

## Quick Start

### For Claude Users

Run this to connect all databases to Claude Code and Claude Desktop:

```bash
curl -sL https://private-mcp.propiolatam.com/setup/script | bash
```

Then restart Claude. Ask things like:
- *"List the tables in the creditu database"*
- *"Show me the top 10 debtors by total amount"*
- *"How many unique customers paid in January?"*

### For Uploaders

1. Go to [private-mcp.propiolatam.com/portal](https://private-mcp.propiolatam.com/portal/)
2. Log in with your authorized email (Cloudflare Access)
3. Click **Upload Data** → select source name → drag & drop Excel files
4. Data is available to Claude within a minute

## Architecture

```
                        ┌──────────────────────────┐
                        │     Cloudflare Edge       │
                        │  SSL · WAF · DDoS · CDN   │
                        └────────────┬─────────────┘
                                     │
                        ┌────────────┴─────────────┐
                        │   Cloudflare Access       │
                        │  /portal/* → email OTP    │
                        └────────────┬─────────────┘
                                     │
                        ┌────────────┴─────────────┐
                        │   Cloudflare Tunnel       │
                        │   (cloudflared on VM)     │
                        └────────────┬─────────────┘
                                     │
┌────────────────────────────────────┼────────────────────────┐
│  GCE VM (e2-micro)                 │                        │
│                                    ▼                        │
│  nginx (127.0.0.1:80) → uvicorn (127.0.0.1:8000)          │
│                                    │                        │
│                           ┌────────┴────────┐               │
│                           │    FastAPI App   │               │
│                           │                  │               │
│                           │  /portal/*       │  Upload UI    │
│                           │  /mcp/{db_id}    │  MCP servers  │
│                           │  /api/*          │  REST API     │
│                           │  /setup/*        │  Config gen   │
│                           └────────┬────────┘               │
│                                    │                        │
│                    ┌───────────────┼───────────────┐        │
│                    ▼               ▼               ▼        │
│              PostgreSQL      SQLite Registry    Static       │
│              (databases)     (config + audit)   (CSS/JS)     │
└─────────────────────────────────────────────────────────────┘
```

## Portal Features

| Feature | Description |
|---|---|
| **Upload & Process** | Drag & drop Excel files, background processing, SSE progress tracking |
| **Data Preview** | See first 5 rows before uploading (client-side, via SheetJS) |
| **Replace Warning** | Warns when uploading to an existing source name |
| **Database Dashboard** | Cards with table count, row count, MCP endpoint, last updated |
| **Database Detail** | Column schemas, sample data, row counts |
| **Download Excel** | Export any database back to `.xlsx` |
| **Search/Filter** | Real-time search across databases |
| **Activity Feed** | Shows recent MCP queries from Claude |
| **Dark Mode** | Toggle in navbar, persisted in localStorage |
| **Spanish/English** | Language toggle, defaults to Spanish |
| **Disk Usage** | Footer indicator showing VM disk space |
| **Audit Log** | Full upload history with user, status, timestamps |

## Security

| Layer | Provider |
|---|---|
| HTTPS | Cloudflare (auto-renewing) |
| Authentication | Cloudflare Access (email OTP) |
| WAF / DDoS | Cloudflare (free tier) |
| Network | Cloudflare Tunnel (no public ports on VM) |
| API Auth | API key (`X-API-Key` header or `?token=` param) |
| SQL Safety | Read-only transactions, write operations blocked |
| File Validation | Extension check, size limit (50 MB) |
| Upload Isolation | One upload at a time (semaphore) |

## MCP Tools

Each database exposes 4 tools to Claude:

| Tool | Description |
|---|---|
| `list_tables` | List all tables in the database |
| `describe_table` | Get column names, types, constraints |
| `query` | Execute a read-only SQL query (max 500 rows) |
| `sample_data` | Get sample rows from a table |

## API Endpoints

### Public (no auth)
| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check |
| GET | `/setup/script` | Bash setup script for Claude Code/Desktop |
| GET | `/setup/mcp-config.json` | JSON config for manual MCP setup |

### Portal (Cloudflare Access)
| Method | Path | Description |
|---|---|---|
| GET | `/portal/` | Dashboard |
| GET | `/portal/upload` | Upload form |
| POST | `/portal/upload` | Process upload |
| GET | `/portal/databases/{id}` | Database detail |
| GET | `/portal/databases/{id}/download` | Export as Excel |
| POST | `/portal/databases/{id}/delete` | Delete database |

### REST API (API key required)
| Method | Path | Description |
|---|---|---|
| POST | `/api/databases` | Register a database |
| GET | `/api/databases` | List databases |
| GET | `/api/databases/{id}/tables` | List tables |
| POST | `/api/databases/{id}/query` | Run SQL query |

### MCP (API key via `?token=`)
| Method | Path | Description |
|---|---|---|
| POST | `/mcp/{db_id}` | Streamable HTTP MCP endpoint |
| GET | `/mcp/{db_id}/sse` | Legacy SSE MCP endpoint |

## Project Structure

```
mcp-data-bridge/
├── app/
│   ├── main.py              # FastAPI app, MCP middleware, setup endpoints
│   ├── config.py            # Settings (env vars)
│   ├── auth.py              # API key dependency
│   ├── db_registry.py       # SQLite: database connection configs
│   ├── db_pool.py           # asyncpg pool manager, query helpers
│   ├── mcp_handler.py       # MCP server factory (4 tools per DB)
│   ├── api/
│   │   ├── admin.py         # POST/GET/DELETE /api/databases
│   │   └── query.py         # Tables, schema, data, SQL queries
│   ├── portal/
│   │   ├── routes.py        # Upload portal routes
│   │   └── audit.py         # Upload + query audit logging
│   ├── services/
│   │   └── excel_loader.py  # Excel → PostgreSQL pipeline
│   ├── templates/           # Jinja2 HTML templates
│   └── static/              # CSS
├── deploy/
│   ├── setup-vm.sh          # One-time VM setup (PostgreSQL, nginx, systemd)
│   ├── deploy.sh            # Pull + restart service
│   ├── setup-tunnel.sh      # Cloudflare Tunnel setup
│   └── load-data.sh         # Manual data loader (legacy)
├── .github/workflows/
│   ├── deploy.yml           # Auto-deploy on push to main
│   ├── setup-tunnel.yml     # One-time tunnel setup (workflow_dispatch)
│   └── run-command.yml      # Run arbitrary command on VM
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── PORTAL-GUIDE.md          # Guide for uploaders
```

## Deployment

Pushes to `main` auto-deploy via GitHub Actions → SSH → `deploy/deploy.sh`.

### Infrastructure
- **VM:** GCE e2-micro (1 GB RAM + 2 GB swap)
- **PostgreSQL:** v16, local
- **Python:** 3.11+ with FastAPI + uvicorn
- **Proxy:** nginx → uvicorn, behind Cloudflare Tunnel

### Environment Variables (`.env`)

```bash
API_KEYS=your-api-key          # comma-separated
REGISTRY_PATH=./data/registry.db
ENVIRONMENT=production          # enables Cloudflare Access header checks
MAX_UPLOAD_SIZE_MB=50
```

## Adding a New Database

### Via Portal (recommended)
Upload Excel files at [private-mcp.propiolatam.com/portal/upload](https://private-mcp.propiolatam.com/portal/upload)

### Via API
```bash
curl -X POST https://private-mcp.propiolatam.com/api/databases \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_KEY" \
  -d '{"name":"my-db", "host":"localhost", "port":5432, "dbname":"mydb", "username":"mcpbridge", "password":"mcpbridge"}'
```

## License

Private — Propio Latam internal use only.
