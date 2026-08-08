from datetime import date, datetime, timezone

import asyncpg

from app.errors import BadRequestError, NotFoundError
from app.repositories import activity as activity_repo
from app.repositories import servers as servers_repo
from app.schemas.activity import ActivityBucket, ActivityResponse, Granularity


async def get_activity(
    conn: asyncpg.Connection,
    server_id: str,
    channel_id: str | None,
    granularity: Granularity,
    date_from: date | None,
    date_to: date | None,
) -> ActivityResponse:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise BadRequestError("'from' must not be after 'to'")

    if await servers_repo.get_server(conn, server_id) is None:
        raise NotFoundError(f"Server '{server_id}' not found")

    if channel_id is not None:
        channel_exists = await conn.fetchval(
            "SELECT 1 FROM channels WHERE channel_id = $1 AND server_id = $2", channel_id, server_id
        )
        if not channel_exists:
            raise NotFoundError(f"Channel '{channel_id}' not found in server '{server_id}'")

    if granularity == "hour":
        rows = await activity_repo.hourly_activity(conn, server_id, channel_id, date_from, date_to)
    elif channel_id is not None:
        rows = await activity_repo.daily_channel_activity(conn, channel_id, date_from, date_to)
    else:
        rows = await activity_repo.daily_server_activity(conn, server_id, date_from, date_to)

    return ActivityResponse(
        server_id=server_id,
        channel_id=channel_id,
        granularity=granularity,
        items=[_to_bucket(r) for r in rows],
    )


def _to_bucket(row: asyncpg.Record) -> ActivityBucket:
    # channel_daily_stats/daily_stats return DATE (day granularity); messages
    # returns TIMESTAMPTZ (hour granularity via date_trunc). Normalize both
    # to datetime so ActivityBucket has one consistent field type.
    bucket = row["bucket"]
    if isinstance(bucket, date) and not isinstance(bucket, datetime):
        bucket = datetime(bucket.year, bucket.month, bucket.day, tzinfo=timezone.utc)
    return ActivityBucket(bucket=bucket, message_count=row["message_count"], active_users=row["active_users"])
