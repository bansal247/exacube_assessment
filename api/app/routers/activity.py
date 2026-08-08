from datetime import date

from asyncpg import Connection
from fastapi import APIRouter, Depends, Query

from app.routers.deps import get_connection
from app.schemas.activity import ActivityResponse, Granularity
from app.services import activity as activity_service

router = APIRouter(prefix="/servers/{server_id}/activity", tags=["activity"])


@router.get("", response_model=ActivityResponse)
async def get_activity(
    server_id: str,
    channel_id: str | None = Query(default=None),
    granularity: Granularity = Query(default="day"),
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    conn: Connection = Depends(get_connection),
) -> ActivityResponse:
    return await activity_service.get_activity(conn, server_id, channel_id, granularity, date_from, date_to)
