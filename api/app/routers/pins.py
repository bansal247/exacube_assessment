import json
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, status
from fastapi.responses import Response

from app.routers.deps import get_agent_connection
from app.schemas.pins import Pin, PinCreate, PinList, ReorderRequest
from app.services import pins as pins_service

router = APIRouter(prefix="/pins", tags=["pins"])


def _to_pin(record: asyncpg.Record) -> Pin:
    row = dict(record)
    row["call_chain"] = json.loads(row["call_chain"])
    row["cached_data"] = json.loads(row["cached_data"])
    return Pin(**row)


@router.post("", response_model=Pin, status_code=status.HTTP_201_CREATED)
async def create_pin(body: PinCreate, conn: asyncpg.Connection = Depends(get_agent_connection)) -> Pin:
    record = await pins_service.create_pin(conn, body.session_id, body.tool_call_id)
    return _to_pin(record)


@router.get("", response_model=PinList)
async def list_pins(conn: asyncpg.Connection = Depends(get_agent_connection)) -> PinList:
    records = await pins_service.list_pins(conn)
    return PinList(items=[_to_pin(r) for r in records])


@router.delete("/{pin_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pin(pin_id: UUID, conn: asyncpg.Connection = Depends(get_agent_connection)) -> Response:
    await pins_service.delete_pin(conn, pin_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/order", response_model=PinList)
async def reorder_pins(body: ReorderRequest, conn: asyncpg.Connection = Depends(get_agent_connection)) -> PinList:
    records = await pins_service.reorder_pins(conn, body.order)
    return PinList(items=[_to_pin(r) for r in records])


@router.post("/{pin_id}/refresh", response_model=Pin)
async def refresh_pin(pin_id: UUID, conn: asyncpg.Connection = Depends(get_agent_connection)) -> Pin:
    record = await pins_service.refresh_pin(conn, pin_id)
    return _to_pin(record)


@router.get("/{pin_id}/download")
async def download_pin(pin_id: UUID, conn: asyncpg.Connection = Depends(get_agent_connection)) -> Response:
    # Rendered fresh from the cached data on every request, nothing
    # persisted server-side -- see README "Pinning" for why this needed no
    # storage/cleanup/lifecycle story at all.
    file = await pins_service.download_pin(conn, pin_id)
    return Response(
        content=file.content,
        media_type=file.content_type,
        headers={"Content-Disposition": f'attachment; filename="{file.filename}"'},
    )
