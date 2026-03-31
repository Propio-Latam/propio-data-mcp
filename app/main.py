"""MCP Data Bridge — expose PostgreSQL databases as MCP servers + REST API."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mcp.server.sse import SseServerTransport
from starlette.routing import Mount

from app.config import settings
from app.auth import require_api_key
from app.db_registry import get_database, get_database_by_name, list_databases
from app.db_pool import close_all_pools
from app.mcp_handler import create_mcp_server
from app.api.admin import router as admin_router
from app.api.query import router as query_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await close_all_pools()


app = FastAPI(
    title="MCP Data Bridge",
    description="Expose PostgreSQL databases as MCP servers and REST APIs",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin_router)
app.include_router(query_router)


# ---------- Health ----------

@app.get("/health")
async def health():
    dbs = await list_databases()
    return {"status": "ok", "registered_databases": len(dbs)}


# ---------- MCP SSE endpoints (one per database) ----------

@app.get("/mcp/{db_id}/sse")
async def mcp_sse_endpoint(request: Request, db_id: str):
    """SSE endpoint — the MCP client connects here first."""
    config = await get_database(db_id) or await get_database_by_name(db_id)
    if not config:
        return JSONResponse(status_code=404, content={"detail": "Database not found"})

    server = create_mcp_server(config)
    sse = SseServerTransport(f"/mcp/{db_id}/messages/")

    async with sse.connect_sse(request.scope, request.receive, request._send) as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


@app.post("/mcp/{db_id}/messages/")
async def mcp_messages_endpoint(request: Request, db_id: str):
    """POST endpoint — the MCP client sends messages here."""
    config = await get_database(db_id) or await get_database_by_name(db_id)
    if not config:
        return JSONResponse(status_code=404, content={"detail": "Database not found"})

    server = create_mcp_server(config)
    sse = SseServerTransport(f"/mcp/{db_id}/messages/")

    async with sse.connect_sse(request.scope, request.receive, request._send) as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


# ---------- Convenience: list MCP endpoints ----------

@app.get("/mcp", dependencies=[Depends(require_api_key)])
async def list_mcp_endpoints():
    """List all available MCP endpoints."""
    dbs = await list_databases()
    return {
        "endpoints": [
            {
                "name": db.name,
                "description": db.description,
                "sse_url": f"/mcp/{db.id}/sse",
                "database_id": db.id,
            }
            for db in dbs
        ]
    }
