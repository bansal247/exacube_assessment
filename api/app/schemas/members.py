from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.common import Page

MemberSortField = Literal["messages_sent", "voice_minutes", "join_date", "last_active"]
SortOrder = Literal["asc", "desc"]


class Member(BaseModel):
    user_id: str
    server_id: str
    username: str
    display_name: str
    is_bot: bool
    join_date: datetime
    last_active: datetime
    roles: list[str]
    messages_sent: int
    voice_minutes: int
    is_owner: bool


class MemberList(BaseModel):
    items: list[Member]
    page: Page
