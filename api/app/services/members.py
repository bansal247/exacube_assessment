import asyncpg

from app.errors import NotFoundError
from app.repositories import members as members_repo
from app.repositories import servers as servers_repo
from app.schemas.common import Page
from app.schemas.members import Member, MemberList, MemberSortField, SortOrder


async def list_members_for_server(
    conn: asyncpg.Connection,
    server_id: str,
    limit: int,
    offset: int,
    sort_by: MemberSortField,
    order: SortOrder,
) -> MemberList:
    if await servers_repo.get_server(conn, server_id) is None:
        raise NotFoundError(f"Server '{server_id}' not found")

    rows, total = await members_repo.list_members_for_server(conn, server_id, limit, offset, sort_by, order)
    return MemberList(
        items=[Member(**dict(r)) for r in rows],
        page=Page(total=total, limit=limit, offset=offset),
    )
