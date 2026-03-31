from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader

from app.config import settings

_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(key: str | None = Security(_header)) -> str:
    if not settings.valid_api_keys:
        return "no-auth"  # allow open access if no keys configured
    if key and key in settings.valid_api_keys:
        return key
    raise HTTPException(status_code=401, detail="Invalid or missing API key")
