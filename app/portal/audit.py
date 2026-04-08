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


async def _get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(settings.registry_path)
    db.row_factory = aiosqlite.Row
    await db.execute(_AUDIT_SCHEMA)
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
