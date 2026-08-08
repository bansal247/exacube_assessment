import asyncpg

from app.errors import NotFoundError
from app.repositories import servers as servers_repo
from app.schemas.common import Page
from app.schemas.servers import Server, ServerList


async def list_servers(conn: asyncpg.Connection, limit: int, offset: int) -> ServerList:
    rows, total = await servers_repo.list_servers(conn, limit, offset)
    return ServerList(
        items=[Server(**dict(r)) for r in rows],
        page=Page(total=total, limit=limit, offset=offset),
    )


async def get_server(conn: asyncpg.Connection, server_id: str) -> Server:
    row = await servers_repo.get_server(conn, server_id)
    if row is None:
        raise NotFoundError(f"Server '{server_id}' not found")
    return Server(**dict(row))
