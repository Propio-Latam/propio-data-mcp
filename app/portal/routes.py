"""Portal routes — dashboard, upload, database detail, delete, setup scripts."""

import asyncio
import io
import json
import os
import shutil
import tempfile

from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.db_registry import list_databases, get_database, get_database_by_name, delete_database
from app.db_pool import list_tables, describe_table, sample_data, close_pool, get_pool, run_query
from app.services.excel_loader import process_upload, process_upload_from_dir, drop_pg_database, UploadError
from app.portal.audit import (
    log_upload,
    get_upload_history,
    get_last_upload_timestamps,
    get_query_history,
    check_source_exists,
)

router = APIRouter(prefix="/portal", tags=["portal"])

_template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=_template_dir)

# Module-level dict to store SSE progress state per source_name
_upload_progress: dict[str, dict] = {}


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
    last_updated_map = await get_last_upload_timestamps()

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
                "last_updated": last_updated_map.get(db.name),
            })
        except Exception:
            db_stats.append({
                "config": db,
                "table_count": 0,
                "total_rows": 0,
                "last_updated": last_updated_map.get(db.name),
            })

    history = await get_upload_history(limit=10)
    query_history = await get_query_history(limit=10)
    has_processing = any(h["status"] == "processing" for h in history)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "databases": db_stats,
        "history": history,
        "query_history": query_history,
        "has_processing": has_processing,
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


async def _background_process(tmp_dir: str, source_name: str, description: str, file_names: list[str], user_email: str):
    """Process uploaded files in background after HTTP response is sent."""
    # Initialize progress state
    _upload_progress[source_name] = {"status": "processing", "progress": 5, "message": "Iniciando procesamiento..."}

    try:
        _upload_progress[source_name] = {"status": "processing", "progress": 20, "message": "Leyendo archivos Excel..."}
        result = await process_upload_from_dir(
            source_name=source_name,
            description=description,
            tmp_dir=tmp_dir,
            file_names=file_names,
        )
        _upload_progress[source_name] = {
            "status": "success",
            "progress": 100,
            "message": f"¡Completado! {result.row_count:,} filas cargadas exitosamente.",
        }
        await log_upload(
            user_email=user_email,
            source_name=source_name,
            file_names=file_names,
            row_count=result.row_count,
            status="success",
        )
    except Exception as e:
        _upload_progress[source_name] = {
            "status": "error",
            "progress": 0,
            "message": f"Error: {e}",
        }
        await log_upload(
            user_email=user_email,
            source_name=source_name,
            file_names=file_names,
            row_count=0,
            status="error",
            error=str(e),
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.post("/upload")
async def handle_upload(
    request: Request,
    source_name: str = Form(...),
    description: str = Form(""),
    files: list[UploadFile] = File(...),
):
    """Handle file upload — save files to disk and return immediately.

    Processing happens in the background to avoid Cloudflare's 100s timeout.
    """
    user_email = _get_user_email(request)
    # Sanitize filenames to prevent path traversal (H1)
    file_names = [os.path.basename(f.filename or "unknown") for f in files]

    # Validate file extensions before saving
    for fname in file_names:
        ext = os.path.splitext(fname)[1].lower()
        if ext not in {".xlsx", ".xls"}:
            return RedirectResponse(
                url=f"/portal/upload?error=Invalid file type. Only .xlsx and .xls are allowed.",
                status_code=303,
            )

    # Save files to disk immediately (fast — just writing bytes)
    tmp_dir = tempfile.mkdtemp(prefix="mcp_upload_")
    try:
        for f, safe_name in zip(files, file_names):
            path = os.path.join(tmp_dir, safe_name)
            with open(path, "wb") as out:
                shutil.copyfileobj(f.file, out)
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return RedirectResponse(
            url=f"/portal/upload?error=Failed to save files: {e}",
            status_code=303,
        )

    # Set initial progress state
    _upload_progress[source_name] = {"status": "processing", "progress": 2, "message": "Archivos recibidos, iniciando..."}

    # Fire and forget — process in background
    asyncio.create_task(_background_process(tmp_dir, source_name, description, file_names, user_email))

    await log_upload(
        user_email=user_email,
        source_name=source_name,
        file_names=file_names,
        row_count=0,
        status="processing",
    )

    return RedirectResponse(
        url=f"/portal/upload/status-page/{source_name}",
        status_code=303,
    )


@router.get("/upload/status-page/{source_name}", response_class=HTMLResponse)
async def upload_status_page(request: Request, source_name: str):
    """Status page that streams real-time processing progress via SSE."""
    return templates.TemplateResponse("upload_status.html", {
        "request": request,
        "source_name": source_name,
        "user_email": _get_user_email(request),
    })


@router.get("/upload/status/{source_name}")
async def upload_status_sse(request: Request, source_name: str):
    """SSE endpoint that streams upload processing progress."""

    async def event_generator():
        last_progress = -1
        max_polls = 300  # 5 minutes at 1s intervals
        poll_count = 0

        while poll_count < max_polls:
            if await request.is_disconnected():
                break

            state = _upload_progress.get(source_name)

            if state is None:
                # Check DB for final status
                history = await get_upload_history(limit=20)
                matching = [h for h in history if h["source_name"] == source_name]
                if matching:
                    latest = matching[0]
                    if latest["status"] == "success":
                        data = json.dumps({"status": "success", "progress": 100, "message": f"Completado: {latest['row_count']:,} filas."})
                        yield f"data: {data}\n\n"
                        break
                    elif latest["status"] == "error":
                        data = json.dumps({"status": "error", "progress": 0, "message": latest.get("error_message") or "Error desconocido"})
                        yield f"data: {data}\n\n"
                        break
                # Still pending
                data = json.dumps({"status": "processing", "progress": 1, "message": "Esperando inicio..."})
                yield f"data: {data}\n\n"
            else:
                current_progress = state.get("progress", 0)
                if current_progress != last_progress or state.get("status") in ("success", "error"):
                    last_progress = current_progress
                    data = json.dumps(state)
                    yield f"data: {data}\n\n"

                    if state.get("status") in ("success", "error"):
                        # Clean up after sending final state
                        _upload_progress.pop(source_name, None)
                        break

            poll_count += 1
            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/databases/{db_id}", response_class=HTMLResponse)
async def database_detail(request: Request, db_id: str):
    """Database detail page — tables, schemas, sample data."""
    config = await get_database(db_id) or await get_database_by_name(db_id)
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


@router.get("/databases/{db_id}/download")
async def download_database(request: Request, db_id: str):
    """Download all data from the first table of a database as an .xlsx file."""
    try:
        import openpyxl
    except ImportError:
        raise HTTPException(500, "openpyxl is not installed. Run: pip install openpyxl")

    config = await get_database(db_id) or await get_database_by_name(db_id)
    if not config:
        raise HTTPException(404, "Database not found")

    try:
        tables = await list_tables(config)
    except Exception as e:
        raise HTTPException(500, f"Could not list tables: {e}")

    if not tables:
        raise HTTPException(404, "No tables found in this database")

    # Use first table
    first_table = tables[0]
    table_name = first_table["table_name"]
    schema_name = first_table["table_schema"]

    try:
        columns = await describe_table(config, table_name, schema_name)
        pool = await get_pool(config)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f'SELECT * FROM "{schema_name}"."{table_name}" LIMIT 100000'
            )
    except Exception as e:
        raise HTTPException(500, f"Could not read table data: {e}")

    # Build xlsx in memory
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = table_name[:31]  # Excel sheet name max 31 chars

    # Header row
    col_names = [c["column_name"] for c in columns]
    ws.append(col_names)

    # Data rows
    for row in rows:
        ws.append([row[c] for c in col_names])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    safe_name = config.name.replace(" ", "_").replace("/", "_")
    filename = f"{safe_name}_{table_name}.xlsx"

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/databases/{db_id}/delete")
async def delete_db(request: Request, db_id: str):
    """Delete a database — drop PG database, remove from registry, close pool."""
    config = await get_database(db_id) or await get_database_by_name(db_id)
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


# ---- API endpoints ----

@router.get("/api/check-source")
async def check_source(name: str):
    """Check if a source_name already exists with data, return row count."""
    result = await check_source_exists(name)
    return JSONResponse(result)


@router.get("/api/disk-usage")
async def disk_usage():
    """Return disk usage stats for the root filesystem."""
    usage = shutil.disk_usage("/")
    return JSONResponse({
        "total": usage.total,
        "used": usage.used,
        "free": usage.free,
        "percent": round(usage.used / usage.total * 100, 1),
    })


# Setup endpoints are in app/main.py under /setup/* (outside Cloudflare Access)
