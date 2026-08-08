from datetime import date

import asyncpg


async def daily_channel_activity(
    conn: asyncpg.Connection, channel_id: str, date_from: date | None, date_to: date | None
) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT date AS bucket, message_count, active_users
        FROM channel_daily_stats
        WHERE channel_id = $1
          AND ($2::date IS NULL OR date >= $2)
          AND ($3::date IS NULL OR date <= $3)
        ORDER BY date
        """,
        channel_id,
        date_from,
        date_to,
    )


async def daily_server_activity(
    conn: asyncpg.Connection, server_id: str, date_from: date | None, date_to: date | None
) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT date AS bucket, total_messages AS message_count, active_members AS active_users
        FROM daily_stats
        WHERE server_id = $1
          AND ($2::date IS NULL OR date >= $2)
          AND ($3::date IS NULL OR date <= $3)
        ORDER BY date
        """,
        server_id,
        date_from,
        date_to,
    )


async def hourly_activity(
    conn: asyncpg.Connection,
    server_id: str,
    channel_id: str | None,
    date_from: date | None,
    date_to: date | None,
) -> list[asyncpg.Record]:
    # messages_sample is a sample (max 5000 rows across all servers), not the
    # full message log -- hourly counts computed from it approximate true
    # hourly volume rather than represent it exactly, unlike the day-grain
    # tables above which are genuine pre-aggregated totals. Documented in
    # README.
    return await conn.fetch(
        """
        SELECT date_trunc('hour', "timestamp") AS bucket,
               COUNT(*) AS message_count,
               COUNT(DISTINCT user_id) AS active_users
        FROM messages
        WHERE server_id = $1
          AND ($2::text IS NULL OR channel_id = $2)
          AND ($3::date IS NULL OR "timestamp" >= $3)
          AND ($4::date IS NULL OR "timestamp" < $4 + INTERVAL '1 day')
        GROUP BY bucket
        ORDER BY bucket
        """,
        server_id,
        channel_id,
        date_from,
        date_to,
    )
