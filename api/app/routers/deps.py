from collections.abc import AsyncIterator

import asyncpg
from fastapi import Request

from app.agent.service import ChatService
from app.db import get_agent_pool, get_pool


async def get_connection() -> AsyncIterator[asyncpg.Connection]:
    pool = get_pool()
    async with pool.acquire() as conn:
        yield conn


async def get_agent_connection() -> AsyncIterator[asyncpg.Connection]:
    pool = get_agent_pool()
    async with pool.acquire() as conn:
        yield conn


def get_chat_service(request: Request) -> ChatService:
    return request.app.state.chat_service
