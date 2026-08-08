from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.db import get_pool

router = APIRouter()


@router.get("/health")
async def health() -> JSONResponse:
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unhealthy", "database": "unreachable"},
        )
    return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ok", "database": "reachable"})
