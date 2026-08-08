from asyncpg import Connection
from fastapi import APIRouter, Depends, Query

from app.routers.deps import get_connection
from app.schemas.common import DEFAULT_LIMIT, MAX_LIMIT
from app.schemas.members import MemberList, MemberSortField, SortOrder
from app.services import members as members_service

router = APIRouter(prefix="/servers/{server_id}/members", tags=["members"])


@router.get("", response_model=MemberList)
async def list_members(
    server_id: str,
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    sort_by: MemberSortField = Query(default="messages_sent"),
    order: SortOrder = Query(default="desc"),
    conn: Connection = Depends(get_connection),
) -> MemberList:
    return await members_service.list_members_for_server(conn, server_id, limit, offset, sort_by, order)
