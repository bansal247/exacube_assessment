from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import Page


class Server(BaseModel):
    server_id: str
    server_name: str
    owner_id: str
    creation_date: datetime
    region: str
    verification_level: int
    premium_tier: int
    premium_subscription_count: int
    approximate_member_count: int
    approximate_presence_count: int


class ServerList(BaseModel):
    items: list[Server]
    page: Page
