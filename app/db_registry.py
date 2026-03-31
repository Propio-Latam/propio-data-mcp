"""SQLite-backed registry that stores PostgreSQL connection configs."""

import aiosqlite
import json
import os
import uuid
from dataclasses import dataclass

from app.config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS databases (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    host        TEXT NOT NULL,
    port        INTEGER NOT NULL DEFAULT 5432,
    dbname      TEXT NOT NULL,
    username    TEXT NOT NULL,
    password    TEXT NOT NULL DEFAULT '',
    ssl         INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@dataclass
class DatabaseConfig:
    id: str
    name: str
    description: str
    host: str
    port: int
    dbname: str
    username: str
    password: str
    ssl: bool
    created_at: str | None = None

    @property
    def dsn(self) -> str:
        ssl_param = "?sslmode=require" if self.ssl else ""
        pwd = f":{self.password}" if self.password else ""
        return f"postgresql://{self.username}{pwd}@{self.host}:{self.port}/{self.dbname}{ssl_param}"

    def to_dict(self, hide_password: bool = True) -> dict:
        d = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
            "username": self.username,
            "ssl": self.ssl,
            "created_at": self.created_at,
        }
        if not hide_password:
            d["password"] = self.password
        return d


async def _get_db() -> aiosqlite.Connection:
    os.makedirs(os.path.dirname(settings.registry_path) or ".", exist_ok=True)
    db = await aiosqlite.connect(settings.registry_path)
    db.row_factory = aiosqlite.Row
    await db.execute(_SCHEMA)
    await db.commit()
    return db


async def add_database(
    name: str,
    host: str,
    port: int,
    dbname: str,
    username: str,
    password: str = "",
    description: str = "",
    ssl: bool = False,
) -> DatabaseConfig:
    db = await _get_db()
    try:
        db_id = uuid.uuid4().hex[:12]
        await db.execute(
            "INSERT INTO databases (id, name, description, host, port, dbname, username, password, ssl) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (db_id, name, description, host, port, dbname, username, password, int(ssl)),
        )
        await db.commit()
        return DatabaseConfig(
            id=db_id, name=name, description=description,
            host=host, port=port, dbname=dbname,
            username=username, password=password, ssl=ssl,
        )
    finally:
        await db.close()


async def list_databases() -> list[DatabaseConfig]:
    db = await _get_db()
    try:
        cursor = await db.execute("SELECT * FROM databases ORDER BY created_at")
        rows = await cursor.fetchall()
        return [
            DatabaseConfig(
                id=r["id"], name=r["name"], description=r["description"],
                host=r["host"], port=r["port"], dbname=r["dbname"],
                username=r["username"], password=r["password"],
                ssl=bool(r["ssl"]), created_at=r["created_at"],
            )
            for r in rows
        ]
    finally:
        await db.close()


async def get_database(db_id: str) -> DatabaseConfig | None:
    db = await _get_db()
    try:
        cursor = await db.execute("SELECT * FROM databases WHERE id = ?", (db_id,))
        r = await cursor.fetchone()
        if not r:
            return None
        return DatabaseConfig(
            id=r["id"], name=r["name"], description=r["description"],
            host=r["host"], port=r["port"], dbname=r["dbname"],
            username=r["username"], password=r["password"],
            ssl=bool(r["ssl"]), created_at=r["created_at"],
        )
    finally:
        await db.close()


async def get_database_by_name(name: str) -> DatabaseConfig | None:
    db = await _get_db()
    try:
        cursor = await db.execute("SELECT * FROM databases WHERE name = ?", (name,))
        r = await cursor.fetchone()
        if not r:
            return None
        return DatabaseConfig(
            id=r["id"], name=r["name"], description=r["description"],
            host=r["host"], port=r["port"], dbname=r["dbname"],
            username=r["username"], password=r["password"],
            ssl=bool(r["ssl"]), created_at=r["created_at"],
        )
    finally:
        await db.close()


async def delete_database(db_id: str) -> bool:
    db = await _get_db()
    try:
        cursor = await db.execute("DELETE FROM databases WHERE id = ?", (db_id,))
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()
