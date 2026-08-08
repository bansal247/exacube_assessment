from pydantic import BaseModel

from app.schemas.common import Page


class Channel(BaseModel):
    channel_id: str
    server_id: str
    channel_name: str
    channel_type: str
    topic: str | None
    nsfw: bool
    rate_limit_per_user: int
    position: int


class ChannelList(BaseModel):
    items: list[Channel]
    page: Page
