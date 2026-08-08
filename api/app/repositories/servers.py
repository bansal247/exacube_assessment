import asyncpg


async def list_servers(conn: asyncpg.Connection, limit: int, offset: int) -> tuple[list[asyncpg.Record], int]:
    rows = await conn.fetch(
        """
        SELECT server_id, server_name, owner_id, creation_date, region,
               verification_level, premium_tier, premium_subscription_count,
               approximate_member_count, approximate_presence_count
        FROM servers
        ORDER BY server_id
        LIMIT $1 OFFSET $2
        """,
        limit,
        offset,
    )
    total = await conn.fetchval("SELECT COUNT(*) FROM servers")
    return rows, total


async def get_server(conn: asyncpg.Connection, server_id: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT server_id, server_name, owner_id, creation_date, region,
               verification_level, premium_tier, premium_subscription_count,
               approximate_member_count, approximate_presence_count
        FROM servers
        WHERE server_id = $1
        """,
        server_id,
    )
