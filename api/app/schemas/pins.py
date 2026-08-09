from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class PinCreate(BaseModel):
    session_id: UUID
    # tool_call_id of any successful, artifact-producing call earlier in
    # that session -- not chart-specific. What plugin produced it, and how
    # it displays, is derived server-side from the registry.
    tool_call_id: str


class ChainStepOut(BaseModel):
    tool_call_id: str
    plugin_name: str
    arguments: dict


class Pin(BaseModel):
    pin_id: UUID
    session_id: UUID
    plugin_name: str
    display_kind: Literal["table", "chart", "image", "file"]
    title: str
    call_chain: list[ChainStepOut]
    cached_data: Any
    cached_at: datetime
    position: int
    created_at: datetime


class PinList(BaseModel):
    items: list[Pin]


class ReorderRequest(BaseModel):
    # Must contain exactly the current set of pin_ids, in the desired order.
    order: list[UUID] = Field(min_length=1)
