"""Async connection pool manager for registered PostgreSQL databases."""

import asyncpg
from app.db_registry import DatabaseConfig

_pools: dict[str, asyncpg.Pool] = {}

MAX_ROWS = 500
STATEMENT_TIMEOUT_MS = 10_000


async def get_pool(config: DatabaseConfig) -> asyncpg.Pool:
    if config.id not in _pools:
        _pools[config.id] = await asyncpg.create_pool(
            dsn=config.dsn,
            min_size=1,
            max_size=5,
            command_timeout=STATEMENT_TIMEOUT_MS / 1000,
        )
    return _pools[config.id]


async def close_pool(db_id: str) -> None:
    pool = _pools.pop(db_id, None)
    if pool:
        await pool.close()


async def close_all_pools() -> None:
    for pool in _pools.values():
        await pool.close()
    _pools.clear()


async def run_query(config: DatabaseConfig, sql: str, params: list | None = None) -> list[dict]:
    """Execute a read-only query and return rows as dicts. Enforces row limit."""
    pool = await get_pool(config)
    async with pool.acquire() as conn:
        # Force read-only transaction
        async with conn.transaction(readonly=True):
            stmt = await conn.prepare(sql)
            columns = [a.name for a in stmt.get_attributes()]
            rows = await stmt.fetch(*params if params else [], timeout=STATEMENT_TIMEOUT_MS / 1000)
            result = []
            for row in rows[:MAX_ROWS]:
                result.append({col: _serialize(row[col]) for col in columns})
            return result


async def list_tables(config: DatabaseConfig) -> list[dict]:
    pool = await get_pool(config)
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT table_schema, table_name,
                   (SELECT COUNT(*) FROM information_schema.columns c
                    WHERE c.table_schema = t.table_schema AND c.table_name = t.table_name) as column_count
            FROM information_schema.tables t
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
              AND table_type = 'BASE TABLE'
            ORDER BY table_schema, table_name
        """)
        return [dict(r) for r in rows]


async def describe_table(config: DatabaseConfig, table: str, schema: str = "public") -> list[dict]:
    pool = await get_pool(config)
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT column_name, data_type, is_nullable, column_default,
                   character_maximum_length, numeric_precision
            FROM information_schema.columns
            WHERE table_schema = $1 AND table_name = $2
            ORDER BY ordinal_position
        """, schema, table)
        return [dict(r) for r in rows]


async def sample_data(config: DatabaseConfig, table: str, schema: str = "public", limit: int = 10) -> list[dict]:
    pool = await get_pool(config)
    limit = min(limit, MAX_ROWS)
    # Validate table name to prevent injection
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema=$1 AND table_name=$2)",
            schema, table,
        )
        if not exists:
            raise ValueError(f"Table {schema}.{table} does not exist")
        rows = await conn.fetch(
            f'SELECT * FROM "{schema}"."{table}" LIMIT {limit}'
        )
        if not rows:
            return []
        columns = list(rows[0].keys())
        return [{col: _serialize(row[col]) for col in columns} for row in rows]


def _serialize(value):
    """Make values JSON-safe."""
    if value is None:
        return None
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)
