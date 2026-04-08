"""MCP Data Bridge — expose PostgreSQL databases as MCP servers + REST API."""

import asyncio
import re
import uuid
from contextlib import asynccontextmanager

import json as json_mod

from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.requests import Request as StarletteRequest
from starlette.types import ASGIApp, Receive, Scope, Send
from mcp.server.streamable_http import StreamableHTTPServerTransport
from mcp.server.sse import SseServerTransport

from urllib.parse import urlparse

from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse

from app.config import settings


# ---- Security middleware ----

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses and enforce CSRF origin checks on portal POST."""

    async def dispatch(self, request: Request, call_next):
        # CSRF: block POST requests to /portal/* from foreign origins
        if request.method == "POST" and request.url.path.startswith("/portal/"):
            origin = request.headers.get("origin") or ""
            referer = request.headers.get("referer") or ""
            allowed = settings.base_url
            origin_ok = origin == allowed or origin == ""
            referer_ok = referer.startswith(allowed) or referer == ""
            if not origin_ok and not referer_ok:
                return StarletteResponse("CSRF check failed", status_code=403)

        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
from app.auth import require_api_key
from app.db_registry import get_database, get_database_by_name, list_databases
from app.db_pool import close_all_pools
from app.mcp_handler import create_mcp_server
from app.api.admin import router as admin_router
from app.api.query import router as query_router
from app.portal.routes import router as portal_router


# ---- Session management ----

MAX_MCP_SESSIONS = 100


class SessionState:
    def __init__(self, transport: StreamableHTTPServerTransport):
        self.transport = transport
        self.ready = asyncio.Event()
        self.task: asyncio.Task | None = None


_sessions: dict[str, SessionState] = {}


async def _run_session(session_id: str, server, state: SessionState):
    try:
        async with state.transport.connect() as (read_stream, write_stream):
            state.ready.set()
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        _sessions.pop(session_id, None)


# ---- ASGI middleware for Streamable HTTP MCP ----

_MCP_PATH_RE = re.compile(r"^/mcp/([^/]+)$")


class MCPStreamableMiddleware:
    """Intercepts /mcp/{db_id} and routes directly to StreamableHTTPServerTransport
    as raw ASGI, bypassing FastAPI's response handling."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            match = _MCP_PATH_RE.match(path)
            if match:
                db_id = match.group(1)
                await self._handle_mcp(scope, receive, send, db_id)
                return
        await self.app(scope, receive, send)

    async def _handle_mcp(self, scope: Scope, receive: Receive, send: Send, db_id: str):
        from app.db_registry import get_database, get_database_by_name
        from app.config import settings

        request = StarletteRequest(scope, receive, send)

        # Auth: check X-API-Key header OR ?token= query param
        if settings.valid_api_keys:
            token = (
                request.headers.get("x-api-key")
                or request.query_params.get("token")
            )
            if not token or token not in settings.valid_api_keys:
                await self._send_json(send, 401, {"detail": "Invalid or missing API key"})
                return

        config = await get_database(db_id) or await get_database_by_name(db_id)
        if not config:
            await self._send_json(send, 404, {"detail": "Database not found"})
            return

        method = request.method
        session_id = request.headers.get("mcp-session-id")

        if method == "DELETE":
            state = _sessions.pop(session_id, None)
            if state and state.task:
                state.task.cancel()
            await self._send_json(send, 200, {"detail": "Session closed"})
            return

        # Existing session
        if session_id and session_id in _sessions:
            state = _sessions[session_id]
            await state.transport.handle_request(scope, receive, send)
            return

        if method == "GET":
            await self._send_json(send, 400, {"detail": "Missing or invalid session"})
            return

        # POST without session — new session
        if len(_sessions) >= MAX_MCP_SESSIONS:
            await self._send_json(send, 503, {"detail": "Too many active sessions"})
            return
        new_session_id = uuid.uuid4().hex
        server = create_mcp_server(config)
        transport = StreamableHTTPServerTransport(
            mcp_session_id=new_session_id,
            is_json_response_enabled=True,
        )

        state = SessionState(transport)
        _sessions[new_session_id] = state
        state.task = asyncio.create_task(_run_session(new_session_id, server, state))

        await state.ready.wait()
        await transport.handle_request(scope, receive, send)

    @staticmethod
    async def _send_json(send: Send, status: int, body: dict):
        import json
        data = json.dumps(body).encode()
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                [b"content-type", b"application/json"],
                [b"content-length", str(len(data)).encode()],
            ],
        })
        await send({"type": "http.response.body", "body": data})


# ---- FastAPI app ----

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    for s in _sessions.values():
        if s.task:
            s.task.cancel()
    await close_all_pools()


app = FastAPI(
    title="MCP Data Bridge",
    description="Expose PostgreSQL databases as MCP servers and REST APIs",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
)

# Add MCP middleware FIRST (outermost) so it intercepts before FastAPI routing
app.add_middleware(MCPStreamableMiddleware)

app.add_middleware(SecurityHeadersMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.base_url],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "Accept", "mcp-session-id"],
)

app.include_router(admin_router)
app.include_router(query_router)
app.include_router(portal_router)

import os
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.get("/health")
async def health():
    dbs = await list_databases()
    return {"status": "ok", "registered_databases": len(dbs)}


# ---- Legacy SSE endpoints ----

async def _resolve_db(db_id: str):
    return await get_database(db_id) or await get_database_by_name(db_id)


@app.get("/mcp/{db_id}/sse")
async def mcp_sse_endpoint(request: Request, db_id: str, _key: str = Depends(require_api_key)):
    config = await _resolve_db(db_id)
    if not config:
        return JSONResponse(status_code=404, content={"detail": "Database not found"})
    server = create_mcp_server(config)
    sse = SseServerTransport(f"/mcp/{db_id}/messages/")
    async with sse.connect_sse(request.scope, request.receive, request._send) as (r, w):
        await server.run(r, w, server.create_initialization_options())


@app.post("/mcp/{db_id}/messages/")
async def mcp_messages_endpoint(request: Request, db_id: str, _key: str = Depends(require_api_key)):
    config = await _resolve_db(db_id)
    if not config:
        return JSONResponse(status_code=404, content={"detail": "Database not found"})
    server = create_mcp_server(config)
    sse = SseServerTransport(f"/mcp/{db_id}/messages/")
    async with sse.connect_sse(request.scope, request.receive, request._send) as (r, w):
        await server.run(r, w, server.create_initialization_options())


# ---- Public setup endpoints (outside Cloudflare Access /portal/*) ----

def _setup_api_key():
    return settings.first_api_key

def _setup_base_url():
    return settings.base_url


@app.get("/setup/script", response_class=PlainTextResponse)
async def setup_script():
    """Bash script that configures ALL databases for Claude Code + Desktop."""
    dbs = await list_databases()
    servers = {}
    for db in dbs:
        servers[db.name] = {
            "command": "npx",
            "args": ["-y", "mcp-remote", f"{_setup_base_url()}/mcp/{db.id}?token={_setup_api_key()}"],
        }

    servers_json = json_mod.dumps(servers, indent=2)
    db_list = "\n".join(f'echo "    - {db.name}: {db.description}"' for db in dbs)

    # The python heredoc uses PYEOF without quotes so $FILE expands from bash
    script = f'''#!/usr/bin/env bash
# Propio Data MCP — Auto-generated setup for all databases
# Usage: curl -sL {_setup_base_url()}/setup/script | bash
set -euo pipefail

echo ""
echo "  Propio Data MCP — Setup"
echo "  ========================"
echo ""

if ! command -v npx &>/dev/null; then
    echo "[!] Node.js is required. Install with: brew install node"
    exit 1
fi
if ! command -v python3 &>/dev/null; then
    echo "[!] Python3 is required."
    exit 1
fi

echo "[*] Testing connection..."
HEALTH=$(curl -sf {_setup_base_url()}/health 2>/dev/null || echo "FAIL")
if [ "$HEALTH" = "FAIL" ]; then
    echo "[!] Cannot reach server."
    exit 1
fi
echo "[+] Server is up"
echo ""

SERVERS_JSON='{servers_json}'

merge_config() {{
    local FILE="$1"
    python3 -c "
import json, os, sys

file_path = sys.argv[1]
servers = json.loads(sys.argv[2])

config = {{}}
if os.path.exists(file_path):
    try:
        with open(file_path) as f:
            config = json.load(f)
    except (json.JSONDecodeError, IOError):
        config = {{}}

if 'mcpServers' not in config:
    config['mcpServers'] = {{}}

for name, entry in servers.items():
    config['mcpServers'][name] = entry
    print(f'  [+] Configured: {{name}}')

os.makedirs(os.path.dirname(file_path) or '.', exist_ok=True)
with open(file_path, 'w') as f:
    json.dump(config, f, indent=2)
" "$FILE" "$SERVERS_JSON"
}}

echo "[*] Setting up Claude Code..."
merge_config "$HOME/.claude/settings.json"

echo "[*] Setting up Claude Desktop..."
if [ "$(uname)" = "Darwin" ]; then
    merge_config "$HOME/Library/Application Support/Claude/claude_desktop_config.json"
else
    merge_config "$HOME/.config/Claude/claude_desktop_config.json"
fi

echo ""
echo "  Setup complete! Configured {len(dbs)} database(s):"
{db_list}
echo ""
echo "  Restart Claude Code / Claude Desktop to apply."
echo "  Portal: {_setup_base_url()}/portal/"
echo ""
'''
    return PlainTextResponse(content=script, media_type="text/plain")


@app.get("/setup/mcp-config.json")
async def mcp_config_json():
    """JSON config for all databases — copy into Claude settings."""
    dbs = await list_databases()
    servers = {}
    for db in dbs:
        servers[db.name] = {
            "command": "npx",
            "args": ["-y", "mcp-remote", f"{_setup_base_url()}/mcp/{db.id}?token={_setup_api_key()}"],
        }
    return JSONResponse(content={"mcpServers": servers})


@app.get("/mcp", dependencies=[Depends(require_api_key)])
async def list_mcp_endpoints():
    dbs = await list_databases()
    return {
        "endpoints": [
            {
                "name": db.name,
                "description": db.description,
                "url": f"/mcp/{db.id}",
                "sse_url": f"/mcp/{db.id}/sse",
                "database_id": db.id,
            }
            for db in dbs
        ]
    }
