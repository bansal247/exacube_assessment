from collections.abc import AsyncIterator

import asyncpg

from app.db import get_pool


async def get_connection() -> AsyncIterator[asyncpg.Connection]:
    pool = get_pool()
    async with pool.acquire() as conn:
        yield conn
