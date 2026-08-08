import asyncpg

from app.errors import NotFoundError
from app.repositories import channels as channels_repo
from app.repositories import servers as servers_repo
from app.schemas.channels import Channel, ChannelList
from app.schemas.common import Page


async def list_channels_for_server(
    conn: asyncpg.Connection, server_id: str, limit: int, offset: int
) -> ChannelList:
    if await servers_repo.get_server(conn, server_id) is None:
        raise NotFoundError(f"Server '{server_id}' not found")

    rows, total = await channels_repo.list_channels_for_server(conn, server_id, limit, offset)
    return ChannelList(
        items=[Channel(**dict(r)) for r in rows],
        page=Page(total=total, limit=limit, offset=offset),
    )
