from asyncpg import Connection
from fastapi import APIRouter, Depends, Query

from app.routers.deps import get_connection
from app.schemas.channels import ChannelList
from app.schemas.common import DEFAULT_LIMIT, MAX_LIMIT
from app.services import channels as channels_service

router = APIRouter(prefix="/servers/{server_id}/channels", tags=["channels"])


@router.get("", response_model=ChannelList)
async def list_channels(
    server_id: str,
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    conn: Connection = Depends(get_connection),
) -> ChannelList:
    return await channels_service.list_channels_for_server(conn, server_id, limit, offset)
