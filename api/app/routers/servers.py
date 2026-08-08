from asyncpg import Connection
from fastapi import APIRouter, Depends, Query

from app.routers.deps import get_connection
from app.schemas.common import DEFAULT_LIMIT, MAX_LIMIT
from app.schemas.servers import Server, ServerList
from app.services import servers as servers_service

router = APIRouter(prefix="/servers", tags=["servers"])


@router.get("", response_model=ServerList)
async def list_servers(
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    conn: Connection = Depends(get_connection),
) -> ServerList:
    return await servers_service.list_servers(conn, limit, offset)


@router.get("/{server_id}", response_model=Server)
async def get_server(server_id: str, conn: Connection = Depends(get_connection)) -> Server:
    return await servers_service.get_server(conn, server_id)
