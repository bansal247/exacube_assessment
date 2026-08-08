from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Granularity = Literal["day", "hour"]


class ActivityBucket(BaseModel):
    bucket: datetime
    message_count: int
    active_users: int | None


class ActivityResponse(BaseModel):
    server_id: str
    channel_id: str | None
    granularity: Granularity
    items: list[ActivityBucket]
