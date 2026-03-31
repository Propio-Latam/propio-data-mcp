"""Admin endpoints — register, list, delete databases."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import require_api_key
from app.db_registry import add_database, list_databases, get_database, delete_database
from app.db_pool import close_pool, get_pool

router = APIRouter(prefix="/api/databases", tags=["databases"])


class RegisterRequest(BaseModel):
    name: str
    host: str
    port: int = 5432
    dbname: str
    username: str
    password: str = ""
    description: str = ""
    ssl: bool = False


@router.post("")
async def register(body: RegisterRequest, _key: str = Depends(require_api_key)):
    """Register a new PostgreSQL database."""
    try:
        config = await add_database(**body.model_dump())
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(400, f"Database with name '{body.name}' already exists")
        raise
    # Test the connection
    try:
        pool = await get_pool(config)
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except Exception as e:
        await delete_database(config.id)
        await close_pool(config.id)
        raise HTTPException(400, f"Cannot connect to database: {e}")
    return {"status": "ok", "database": config.to_dict()}


@router.get("")
async def list_all(_key: str = Depends(require_api_key)):
    """List all registered databases."""
    dbs = await list_databases()
    return {"databases": [d.to_dict() for d in dbs]}


@router.get("/{db_id}")
async def get_one(db_id: str, _key: str = Depends(require_api_key)):
    """Get a registered database by ID."""
    config = await get_database(db_id)
    if not config:
        raise HTTPException(404, "Database not found")
    return config.to_dict()


@router.delete("/{db_id}")
async def remove(db_id: str, _key: str = Depends(require_api_key)):
    """Unregister a database."""
    await close_pool(db_id)
    deleted = await delete_database(db_id)
    if not deleted:
        raise HTTPException(404, "Database not found")
    return {"status": "deleted"}
