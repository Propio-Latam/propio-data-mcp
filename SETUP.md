# MCP Data Bridge — Connection Guide

## Server Info

| | |
|---|---|
| **Server IP** | `34.61.255.37` |
| **API Key** | `f4e269254a2bfd08bab1852edf0e13b60b89fc1522649050` |
| **API Docs** | http://34.61.255.37/docs |
| **Health Check** | http://34.61.255.37/health |

## Registered Databases

| Name | Database ID | MCP Endpoint | Description |
|---|---|---|---|
| creditu | `200502546258` | `/mcp/200502546258` | Creditu loan remittance data (252 rows) |

---

## Claude Desktop (Cowork Mode)

MCP tools are only available in **Cowork mode** in Claude Desktop.

### 1. Edit the config file

Open the file:

```
~/Library/Application Support/Claude/claude_desktop_config.json
```

Add the `creditu` entry inside `mcpServers`:

```json
{
  "mcpServers": {
    "creditu": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "http://34.61.255.37/mcp/200502546258",
        "--allow-http"
      ]
    }
  }
}
```

> Requires Node.js installed (`brew install node` if missing).

### 2. Restart Claude Desktop

Quit (Cmd+Q) and reopen Claude Desktop.

### 3. Switch to Cowork mode

Click the mode switcher at the top of the sidebar and select **Cowork**. You should see the Creditu MCP tools (hammer icon).

### 4. Use it

Ask Claude things like:

- "List the tables in the creditu database"
- "Show me the top 10 debtors by total amount"
- "How many unique customers paid in January 2026?"
- "Run: SELECT * FROM remesas WHERE monto_remesado_clp > 300000"

---

## Claude Code (CLI)

### Option A: Add to project settings

Create or edit `.claude/settings.json` in your project:

```json
{
  "mcpServers": {
    "creditu": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "http://34.61.255.37/mcp/200502546258",
        "--allow-http"
      ]
    }
  }
}
```

### Option B: Add to global settings

Edit `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "creditu": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "http://34.61.255.37/mcp/200502546258",
        "--allow-http"
      ]
    }
  }
}
```

Then restart Claude Code. The MCP tools will be available in all conversations.

---

## REST API (curl / apps)

All REST endpoints require the `X-API-Key` header.

### List registered databases

```bash
curl -H "X-API-Key: f4e269254a2bfd08bab1852edf0e13b60b89fc1522649050" \
  http://34.61.255.37/api/databases
```

### List tables in a database

```bash
curl -H "X-API-Key: f4e269254a2bfd08bab1852edf0e13b60b89fc1522649050" \
  http://34.61.255.37/api/databases/200502546258/tables
```

### Get table schema

```bash
curl -H "X-API-Key: f4e269254a2bfd08bab1852edf0e13b60b89fc1522649050" \
  http://34.61.255.37/api/databases/200502546258/tables/remesas/schema
```

### Get sample data

```bash
curl -H "X-API-Key: f4e269254a2bfd08bab1852edf0e13b60b89fc1522649050" \
  "http://34.61.255.37/api/databases/200502546258/tables/remesas/data?limit=10"
```

### Run a SQL query (read-only)

```bash
curl -X POST http://34.61.255.37/api/databases/200502546258/query \
  -H "Content-Type: application/json" \
  -H "X-API-Key: f4e269254a2bfd08bab1852edf0e13b60b89fc1522649050" \
  -d '{"sql": "SELECT nombre_deudor, sum(monto_remesado_clp) as total FROM remesas GROUP BY 1 ORDER BY 2 DESC LIMIT 10"}'
```

### Register a new database

```bash
curl -X POST http://34.61.255.37/api/databases \
  -H "Content-Type: application/json" \
  -H "X-API-Key: f4e269254a2bfd08bab1852edf0e13b60b89fc1522649050" \
  -d '{
    "name": "my-database",
    "host": "localhost",
    "port": 5432,
    "dbname": "mydb",
    "username": "mcpbridge",
    "password": "mcpbridge",
    "description": "Description of the database"
  }'
```

---

## MCP Tools Available

Each registered database exposes 4 tools:

| Tool | Description |
|---|---|
| `list_tables` | List all tables in the database |
| `describe_table` | Get column names, types, and constraints for a table |
| `query` | Execute a read-only SQL query (max 500 rows) |
| `sample_data` | Get sample rows from a table |

All queries are **read-only** — write operations (INSERT, UPDATE, DELETE, DROP, etc.) are blocked.

---

## Security

- All REST endpoints require the `X-API-Key` header
- MCP endpoints are currently open (MCP clients don't support custom headers easily)
- All SQL queries run in read-only transactions
- Write operations are blocked at both the API and database level
- Connection strings are stored in a local SQLite registry on the server

---

## Adding a New Database

1. Upload your data (Excel, CSV, or create a PostgreSQL database directly on the server)
2. Register it via the REST API (see above)
3. A new MCP endpoint is created automatically at `/mcp/{database_id}`
4. Update your Claude Desktop/Code config with the new endpoint
