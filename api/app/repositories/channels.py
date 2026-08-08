import asyncpg


async def list_channels_for_server(
    conn: asyncpg.Connection, server_id: str, limit: int, offset: int
) -> tuple[list[asyncpg.Record], int]:
    rows = await conn.fetch(
        """
        SELECT channel_id, server_id, channel_name, channel_type, topic,
               nsfw, rate_limit_per_user, "position"
        FROM channels
        WHERE server_id = $1
        ORDER BY "position"
        LIMIT $2 OFFSET $3
        """,
        server_id,
        limit,
        offset,
    )
    total = await conn.fetchval("SELECT COUNT(*) FROM channels WHERE server_id = $1", server_id)
    return rows, total
