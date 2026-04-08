"""Excel → PostgreSQL → MCP registration pipeline.

Takes uploaded Excel files, loads them into a new (or existing) PostgreSQL
database, and registers that database with the MCP bridge so it becomes
instantly queryable by Claude.
"""

import asyncio
import os
import re
import shutil
import tempfile
import unicodedata
from dataclasses import dataclass, field

import asyncpg
import pandas as pd
from fastapi import UploadFile
from sqlalchemy import create_engine, text

from app.config import settings
from app.db_registry import add_database, get_database_by_name, delete_database

# Serialize one upload at a time to avoid OOM on e2-micro (1 GB RAM)
_upload_semaphore = asyncio.Semaphore(1)

PG_USER = "mcpbridge"
PG_PASSWORD = "mcpbridge"
PG_HOST = "localhost"
PG_PORT = 5432

MAX_FILE_SIZE_BYTES = settings.max_upload_size_mb * 1024 * 1024
ALLOWED_EXTENSIONS = {".xlsx", ".xls"}


@dataclass
class UploadResult:
    db_id: str
    db_name: str
    table_name: str
    row_count: int
    column_names: list[str]
    mcp_endpoint: str
    source_files: list[str] = field(default_factory=list)


class UploadError(Exception):
    """Raised when file validation or processing fails."""


def normalize_column_name(name: str) -> str:
    """Convert a column name to snake_case ASCII."""
    name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    name = re.sub(r"[^a-zA-Z0-9\s]", "", name)
    return re.sub(r"\s+", "_", name.strip()).lower()


def sanitize_db_name(source_name: str) -> str:
    """Convert a human source name like 'Banco Estado' into a valid PG database name."""
    name = unicodedata.normalize("NFKD", source_name).encode("ascii", "ignore").decode()
    name = re.sub(r"[^a-zA-Z0-9\s]", "", name)
    name = re.sub(r"\s+", "_", name.strip()).lower()
    if not name:
        raise UploadError("Source name must contain at least one alphanumeric character")
    # PG identifiers max 63 chars
    return name[:63]


async def create_pg_database(db_name: str) -> None:
    """Create a PostgreSQL database if it doesn't already exist."""
    conn = await asyncpg.connect(
        user=PG_USER, password=PG_PASSWORD, host=PG_HOST, port=PG_PORT, database="postgres"
    )
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", db_name
        )
        if not exists:
            # CREATE DATABASE cannot run inside a transaction
            await conn.execute(f'CREATE DATABASE "{db_name}" OWNER "{PG_USER}"')
    finally:
        await conn.close()


async def drop_pg_database(db_name: str) -> None:
    """Drop a PostgreSQL database if it exists."""
    conn = await asyncpg.connect(
        user=PG_USER, password=PG_PASSWORD, host=PG_HOST, port=PG_PORT, database="postgres"
    )
    try:
        # Terminate active connections first
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
            db_name,
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
    finally:
        await conn.close()


def _validate_files(files: list[UploadFile]) -> None:
    """Validate file extensions and sizes before processing."""
    if not files:
        raise UploadError("At least one file is required")

    for f in files:
        if not f.filename:
            raise UploadError("File has no filename")
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise UploadError(
                f"File '{f.filename}' has invalid extension '{ext}'. Only .xlsx and .xls are allowed."
            )
        if f.size and f.size > MAX_FILE_SIZE_BYTES:
            raise UploadError(
                f"File '{f.filename}' is {f.size / 1024 / 1024:.1f} MB. Max allowed is {settings.max_upload_size_mb} MB."
            )


def _read_excel_files(tmp_dir: str, files: list[UploadFile]) -> pd.DataFrame:
    """Save uploaded files to disk, read with pandas, return combined DataFrame."""
    all_dfs: list[pd.DataFrame] = []

    for f in files:
        path = os.path.join(tmp_dir, f.filename)
        with open(path, "wb") as out:
            shutil.copyfileobj(f.file, out)

        try:
            df = pd.read_excel(path)
        except Exception as e:
            raise UploadError(f"Cannot read '{f.filename}': {e}")

        if df.empty:
            raise UploadError(f"File '{f.filename}' contains no data")

        df["source_file"] = f.filename
        all_dfs.append(df)

    combined = pd.concat(all_dfs, ignore_index=True)

    # Normalize column names
    combined.columns = [normalize_column_name(c) for c in combined.columns]

    # Deduplicate
    combined.drop_duplicates(keep="first", inplace=True)

    return combined


def _load_to_postgres(db_name: str, table_name: str, df: pd.DataFrame) -> None:
    """Write a DataFrame into a PostgreSQL table, replacing existing data."""
    engine = create_engine(
        f"postgresql+psycopg2://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{db_name}"
    )
    df.to_sql(table_name, engine, if_exists="replace", index=False, method="multi", chunksize=500)

    with engine.connect() as conn:
        # Add serial primary key if not already present
        conn.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN IF NOT EXISTS id SERIAL PRIMARY KEY'))
        conn.commit()

    engine.dispose()


async def process_upload(
    source_name: str,
    description: str,
    files: list[UploadFile],
    table_name: str = "data",
) -> UploadResult:
    """Full pipeline: validate → read Excel → create DB → load → register MCP.

    Uses a semaphore to serialize uploads (one at a time) to avoid OOM on the
    e2-micro VM.
    """
    async with _upload_semaphore:
        _validate_files(files)
        db_name = sanitize_db_name(source_name)

        tmp_dir = tempfile.mkdtemp(prefix="mcp_upload_")
        try:
            # Read files (sync, CPU-bound — run in thread to not block event loop)
            df = await asyncio.to_thread(_read_excel_files, tmp_dir, files)
            row_count = len(df)
            column_names = list(df.columns)

            # Create PG database
            await create_pg_database(db_name)

            # Load data (sync, CPU-bound)
            await asyncio.to_thread(_load_to_postgres, db_name, table_name, df)

            # Register with MCP bridge (or update if already registered)
            existing = await get_database_by_name(db_name)
            if existing:
                # Close pool and re-register to pick up any schema changes
                from app.db_pool import close_pool
                await close_pool(existing.id)
                await delete_database(existing.id)

            config = await add_database(
                name=db_name,
                host=PG_HOST,
                port=PG_PORT,
                dbname=db_name,
                username=PG_USER,
                password=PG_PASSWORD,
                description=description or f"Data uploaded from {source_name}",
                ssl=False,
            )

            return UploadResult(
                db_id=config.id,
                db_name=db_name,
                table_name=table_name,
                row_count=row_count,
                column_names=column_names,
                mcp_endpoint=f"/mcp/{config.id}",
                source_files=[f.filename for f in files],
            )

        finally:
            # Always clean up temp files
            shutil.rmtree(tmp_dir, ignore_errors=True)
            # Reset file positions in case of retry
            for f in files:
                await f.seek(0)
