import asyncpg

# Whitelisted column mapping -- sort_by is user-supplied, and column/direction
# names can't be bound as query parameters in SQL, so we never interpolate
# the raw string. It only ever selects from this fixed map.
_SORT_COLUMNS = {
    "messages_sent": "messages_sent",
    "voice_minutes": "voice_minutes",
    "join_date": "join_date",
    "last_active": "last_active",
}


async def list_members_for_server(
    conn: asyncpg.Connection,
    server_id: str,
    limit: int,
    offset: int,
    sort_by: str,
    order: str,
) -> tuple[list[asyncpg.Record], int]:
    column = _SORT_COLUMNS[sort_by]
    direction = "ASC" if order == "asc" else "DESC"

    rows = await conn.fetch(
        f"""
        SELECT user_id, server_id, username, display_name, is_bot, join_date,
               last_active, roles, messages_sent, voice_minutes, is_owner
        FROM members
        WHERE server_id = $1
        ORDER BY {column} {direction}, user_id
        LIMIT $2 OFFSET $3
        """,
        server_id,
        limit,
        offset,
    )
    total = await conn.fetchval("SELECT COUNT(*) FROM members WHERE server_id = $1", server_id)
    return rows, total
