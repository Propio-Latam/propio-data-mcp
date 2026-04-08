"""Upload audit logging — tracks who uploaded what, when, and the result."""

import json
import aiosqlite
from app.config import settings

_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS upload_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
    user_email  TEXT NOT NULL DEFAULT 'unknown',
    source_name TEXT NOT NULL,
    file_names  TEXT NOT NULL DEFAULT '[]',
    row_count   INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT
);
"""

_QUERY_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS query_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL DEFAULT (datetime('now')),
    db_name         TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    arguments_json  TEXT NOT NULL DEFAULT '{}'
);
"""


async def _get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(settings.registry_path)
    db.row_factory = aiosqlite.Row
    await db.execute(_AUDIT_SCHEMA)
    await db.execute(_QUERY_LOG_SCHEMA)
    await db.commit()
    return db


async def log_upload(
    user_email: str,
    source_name: str,
    file_names: list[str],
    row_count: int,
    status: str,
    error: str | None = None,
) -> None:
    """Record an upload attempt in the audit log."""
    db = await _get_db()
    try:
        await db.execute(
            "INSERT INTO upload_log (user_email, source_name, file_names, row_count, status, error_message) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_email, source_name, json.dumps(file_names), row_count, status, error),
        )
        await db.commit()
    finally:
        await db.close()


async def get_upload_history(limit: int = 50) -> list[dict]:
    """Get recent upload history, newest first."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM upload_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        result = []
        for r in rows:
            entry = dict(r)
            entry["file_names"] = json.loads(entry["file_names"])
            result.append(entry)
        return result
    finally:
        await db.close()


async def get_last_upload_timestamps() -> dict[str, str]:
    """Return a dict mapping source_name -> most recent timestamp for successful/processing uploads."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            """
            SELECT source_name, MAX(timestamp) as last_ts
            FROM upload_log
            WHERE status IN ('success', 'processing')
            GROUP BY source_name
            """
        )
        rows = await cursor.fetchall()
        return {r["source_name"]: r["last_ts"] for r in rows}
    finally:
        await db.close()


async def log_query(db_name: str, tool_name: str, arguments: dict) -> None:
    """Record an MCP tool invocation in query_log."""
    db = await _get_db()
    try:
        await db.execute(
            "INSERT INTO query_log (db_name, tool_name, arguments_json) VALUES (?, ?, ?)",
            (db_name, tool_name, json.dumps(arguments, default=str)),
        )
        await db.commit()
    finally:
        await db.close()


async def get_query_history(limit: int = 10) -> list[dict]:
    """Get last N MCP query log entries, newest first."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM query_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        result = []
        for r in rows:
            entry = dict(r)
            try:
                entry["arguments"] = json.loads(entry["arguments_json"])
            except Exception:
                entry["arguments"] = {}
            result.append(entry)
        return result
    finally:
        await db.close()


async def check_source_exists(source_name: str) -> dict:
    """Check if a source_name already has data in upload_log (successful uploads)."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            """
            SELECT row_count FROM upload_log
            WHERE source_name = ? AND status = 'success'
            ORDER BY timestamp DESC LIMIT 1
            """,
            (source_name,),
        )
        row = await cursor.fetchone()
        if row:
            return {"exists": True, "row_count": row["row_count"]}
        return {"exists": False, "row_count": 0}
    finally:
        await db.close()
