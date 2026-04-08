"""MCP Data Bridge — expose PostgreSQL databases as MCP servers + REST API."""

import asyncio
import re
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import Request as StarletteRequest
from starlette.types import ASGIApp, Receive, Scope, Send
from mcp.server.streamable_http import StreamableHTTPServerTransport
from mcp.server.sse import SseServerTransport

from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.auth import require_api_key
from app.db_registry import get_database, get_database_by_name, list_databases
from app.db_pool import close_all_pools
from app.mcp_handler import create_mcp_server
from app.api.admin import router as admin_router
from app.api.query import router as query_router
from app.portal.routes import router as portal_router


# ---- Session management ----

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
)

# Add MCP middleware FIRST (outermost) so it intercepts before FastAPI routing
app.add_middleware(MCPStreamableMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
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
