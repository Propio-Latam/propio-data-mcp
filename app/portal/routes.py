"""Portal routes — dashboard, upload, database detail, delete."""

import os
from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.db_registry import list_databases, get_database, delete_database
from app.db_pool import list_tables, describe_table, sample_data, close_pool, get_pool
from app.services.excel_loader import process_upload, drop_pg_database, UploadError
from app.portal.audit import log_upload, get_upload_history

router = APIRouter(prefix="/portal", tags=["portal"])

_template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=_template_dir)


def _get_user_email(request: Request) -> str:
    """Extract user email from Cloudflare Access headers.

    In production, Cloudflare Access sets Cf-Access-Authenticated-User-Email.
    In development, fall back to 'dev@localhost'.
    """
    email = request.headers.get("Cf-Access-Authenticated-User-Email")
    if email:
        return email
    if settings.is_production:
        return "unknown"
    return "dev@localhost"


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Dashboard — list all registered databases with stats."""
    dbs = await list_databases()

    db_stats = []
    for db in dbs:
        try:
            tables = await list_tables(db)
            total_rows = 0
            for t in tables:
                try:
                    pool = await get_pool(db)
                    async with pool.acquire() as conn:
                        count = await conn.fetchval(
                            f'SELECT COUNT(*) FROM "{t["table_schema"]}"."{t["table_name"]}"'
                        )
                        total_rows += count or 0
                except Exception:
                    pass
            db_stats.append({
                "config": db,
                "table_count": len(tables),
                "total_rows": total_rows,
            })
        except Exception:
            db_stats.append({
                "config": db,
                "table_count": 0,
                "total_rows": 0,
            })

    history = await get_upload_history(limit=10)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "databases": db_stats,
        "history": history,
        "user_email": _get_user_email(request),
        "message": request.query_params.get("message"),
        "error": request.query_params.get("error"),
    })


@router.get("/upload", response_class=HTMLResponse)
async def upload_form(request: Request):
    """Upload form page."""
    return templates.TemplateResponse("upload.html", {
        "request": request,
        "user_email": _get_user_email(request),
        "max_size_mb": settings.max_upload_size_mb,
        "error": request.query_params.get("error"),
    })


@router.post("/upload")
async def handle_upload(
    request: Request,
    source_name: str = Form(...),
    description: str = Form(""),
    files: list[UploadFile] = File(...),
):
    """Handle file upload — process Excel files and register with MCP."""
    user_email = _get_user_email(request)
    file_names = [f.filename or "unknown" for f in files]

    try:
        result = await process_upload(
            source_name=source_name,
            description=description,
            files=files,
        )
        await log_upload(
            user_email=user_email,
            source_name=source_name,
            file_names=file_names,
            row_count=result.row_count,
            status="success",
        )
        message = (
            f"Uploaded {result.row_count} rows from {len(file_names)} file(s) "
            f"into '{result.db_name}'. MCP endpoint: {result.mcp_endpoint}"
        )
        return RedirectResponse(
            url=f"/portal/?message={message}",
            status_code=303,
        )

    except UploadError as e:
        await log_upload(
            user_email=user_email,
            source_name=source_name,
            file_names=file_names,
            row_count=0,
            status="error",
            error=str(e),
        )
        return RedirectResponse(
            url=f"/portal/upload?error={e}",
            status_code=303,
        )

    except Exception as e:
        await log_upload(
            user_email=user_email,
            source_name=source_name,
            file_names=file_names,
            row_count=0,
            status="error",
            error=str(e),
        )
        return RedirectResponse(
            url=f"/portal/upload?error=Unexpected error: {e}",
            status_code=303,
        )


@router.get("/databases/{db_id}", response_class=HTMLResponse)
async def database_detail(request: Request, db_id: str):
    """Database detail page — tables, schemas, sample data."""
    config = await get_database(db_id)
    if not config:
        raise HTTPException(404, "Database not found")

    tables_info = []
    try:
        tables = await list_tables(config)
        for t in tables:
            table_name = t["table_name"]
            schema_name = t["table_schema"]
            try:
                columns = await describe_table(config, table_name, schema_name)
                rows = await sample_data(config, table_name, schema_name, limit=5)
                pool = await get_pool(config)
                async with pool.acquire() as conn:
                    row_count = await conn.fetchval(
                        f'SELECT COUNT(*) FROM "{schema_name}"."{table_name}"'
                    )
                tables_info.append({
                    "name": table_name,
                    "schema": schema_name,
                    "columns": columns,
                    "sample_rows": rows,
                    "row_count": row_count or 0,
                })
            except Exception:
                tables_info.append({
                    "name": table_name,
                    "schema": schema_name,
                    "columns": [],
                    "sample_rows": [],
                    "row_count": 0,
                })
    except Exception:
        pass

    return templates.TemplateResponse("database_detail.html", {
        "request": request,
        "config": config,
        "tables": tables_info,
        "user_email": _get_user_email(request),
        "message": request.query_params.get("message"),
    })


@router.post("/databases/{db_id}/delete")
async def delete_db(request: Request, db_id: str):
    """Delete a database — drop PG database, remove from registry, close pool."""
    config = await get_database(db_id)
    if not config:
        raise HTTPException(404, "Database not found")

    db_name = config.dbname
    await close_pool(db_id)
    await delete_database(db_id)

    try:
        await drop_pg_database(db_name)
    except Exception:
        pass  # DB may not exist if it was manually removed

    user_email = _get_user_email(request)
    await log_upload(
        user_email=user_email,
        source_name=db_name,
        file_names=[],
        row_count=0,
        status="deleted",
    )

    return RedirectResponse(
        url=f"/portal/?message=Database '{db_name}' deleted",
        status_code=303,
    )
