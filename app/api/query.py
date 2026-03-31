"""Query endpoints — list tables, describe, query, sample data."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth import require_api_key
from app.db_registry import get_database
from app.db_pool import list_tables, describe_table, run_query, sample_data

router = APIRouter(prefix="/api/databases/{db_id}", tags=["query"])


class QueryRequest(BaseModel):
    sql: str


@router.get("/tables")
async def get_tables(db_id: str, _key: str = Depends(require_api_key)):
    """List all tables in the database."""
    config = await get_database(db_id)
    if not config:
        raise HTTPException(404, "Database not found")
    try:
        tables = await list_tables(config)
    except Exception as e:
        raise HTTPException(502, f"Database error: {e}")
    return {"tables": tables}


@router.get("/tables/{table}/schema")
async def get_schema(
    db_id: str,
    table: str,
    schema: str = Query("public"),
    _key: str = Depends(require_api_key),
):
    """Describe a table's columns."""
    config = await get_database(db_id)
    if not config:
        raise HTTPException(404, "Database not found")
    try:
        cols = await describe_table(config, table, schema)
    except Exception as e:
        raise HTTPException(502, f"Database error: {e}")
    if not cols:
        raise HTTPException(404, f"Table {schema}.{table} not found")
    return {"table": table, "schema": schema, "columns": cols}


@router.get("/tables/{table}/data")
async def get_data(
    db_id: str,
    table: str,
    schema: str = Query("public"),
    limit: int = Query(10, ge=1, le=500),
    _key: str = Depends(require_api_key),
):
    """Get sample rows from a table."""
    config = await get_database(db_id)
    if not config:
        raise HTTPException(404, "Database not found")
    try:
        rows = await sample_data(config, table, schema, limit)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(502, f"Database error: {e}")
    return {"table": table, "row_count": len(rows), "rows": rows}


@router.post("/query")
async def execute_query(db_id: str, body: QueryRequest, _key: str = Depends(require_api_key)):
    """Execute a read-only SQL query."""
    config = await get_database(db_id)
    if not config:
        raise HTTPException(404, "Database not found")
    sql = body.sql.strip()
    # Block write operations
    first_word = sql.split()[0].upper() if sql else ""
    if first_word in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE", "GRANT", "REVOKE"):
        raise HTTPException(403, "Only read-only queries are allowed")
    try:
        rows = await run_query(config, sql)
    except Exception as e:
        raise HTTPException(400, f"Query error: {e}")
    return {"row_count": len(rows), "rows": rows}
