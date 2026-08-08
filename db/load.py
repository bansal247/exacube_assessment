"""
Safe to run more than once -- every insert is an upsert keyed on the
table's primary key, so re-running never duplicates rows.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql

DATA_DIR = Path(__file__).resolve().parent / "discord_analytics_dataset"
TS_FMT = "%Y-%m-%d %H:%M:%S"
print(f"loading data from {DATA_DIR}")

def parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, TS_FMT).replace(tzinfo=timezone.utc)


def parse_bool(value: str) -> bool:
    return value.strip().lower() == "true"


def parse_int(value: str, default: int = 0) -> int:
    return int(value) if value not in (None, "") else default


def parse_float(value: str) -> float | None:
    return float(value) if value not in (None, "") else None


def read_csv(name: str) -> list[dict]:
    with open(DATA_DIR / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_servers(cur, rows: list[dict]) -> None:
    stmt = sql.SQL(
        """
        INSERT INTO servers (
            server_id, server_name, owner_id, creation_date, region,
            verification_level, default_message_notifications,
            explicit_content_filter, system_channel_id, afk_channel_id,
            afk_timeout, widget_enabled, premium_tier,
            premium_subscription_count, approximate_member_count,
            approximate_presence_count
        ) VALUES (
            %(server_id)s, %(server_name)s, %(owner_id)s, %(creation_date)s, %(region)s,
            %(verification_level)s, %(default_message_notifications)s,
            %(explicit_content_filter)s, %(system_channel_id)s, %(afk_channel_id)s,
            %(afk_timeout)s, %(widget_enabled)s, %(premium_tier)s,
            %(premium_subscription_count)s, %(approximate_member_count)s,
            %(approximate_presence_count)s
        )
        ON CONFLICT (server_id) DO UPDATE SET
            server_name = EXCLUDED.server_name,
            owner_id = EXCLUDED.owner_id,
            creation_date = EXCLUDED.creation_date,
            region = EXCLUDED.region,
            verification_level = EXCLUDED.verification_level,
            default_message_notifications = EXCLUDED.default_message_notifications,
            explicit_content_filter = EXCLUDED.explicit_content_filter,
            system_channel_id = EXCLUDED.system_channel_id,
            afk_channel_id = EXCLUDED.afk_channel_id,
            afk_timeout = EXCLUDED.afk_timeout,
            widget_enabled = EXCLUDED.widget_enabled,
            premium_tier = EXCLUDED.premium_tier,
            premium_subscription_count = EXCLUDED.premium_subscription_count,
            approximate_member_count = EXCLUDED.approximate_member_count,
            approximate_presence_count = EXCLUDED.approximate_presence_count
        """
    )
    for r in rows:
        cur.execute(
            stmt,
            {
                "server_id": r["server_id"],
                "server_name": r["server_name"],
                "owner_id": r["owner_id"],
                "creation_date": parse_ts(r["creation_date"]),
                "region": r["region"],
                "verification_level": parse_int(r["verification_level"]),
                "default_message_notifications": parse_int(r["default_message_notifications"]),
                "explicit_content_filter": parse_int(r["explicit_content_filter"]),
                "system_channel_id": r["system_channel_id"] or None,
                "afk_channel_id": r["afk_channel_id"] or None,
                "afk_timeout": int(parse_float(r["afk_timeout"])) if r["afk_timeout"] else None,
                "widget_enabled": parse_bool(r["widget_enabled"]),
                "premium_tier": parse_int(r["premium_tier"]),
                "premium_subscription_count": parse_int(r["premium_subscription_count"]),
                "approximate_member_count": parse_int(r["approximate_member_count"]),
                "approximate_presence_count": parse_int(r["approximate_presence_count"]),
            },
        )
    print(f"servers: upserted {len(rows)} rows")


def load_channels(cur, rows: list[dict]) -> None:
    stmt = sql.SQL(
        """
        INSERT INTO channels (
            channel_id, server_id, channel_name, channel_type, topic,
            nsfw, rate_limit_per_user, "position"
        ) VALUES (
            %(channel_id)s, %(server_id)s, %(channel_name)s, %(channel_type)s, %(topic)s,
            %(nsfw)s, %(rate_limit_per_user)s, %(position)s
        )
        ON CONFLICT (channel_id) DO UPDATE SET
            server_id = EXCLUDED.server_id,
            channel_name = EXCLUDED.channel_name,
            channel_type = EXCLUDED.channel_type,
            topic = EXCLUDED.topic,
            nsfw = EXCLUDED.nsfw,
            rate_limit_per_user = EXCLUDED.rate_limit_per_user,
            "position" = EXCLUDED."position"
        """
    )
    for r in rows:
        cur.execute(
            stmt,
            {
                "channel_id": r["channel_id"],
                "server_id": r["server_id"],
                "channel_name": r["channel_name"],
                "channel_type": r["channel_type"],
                "topic": r["topic"] or None,
                "nsfw": parse_bool(r["nsfw"]),
                "rate_limit_per_user": parse_int(r["rate_limit_per_user"]),
                "position": parse_int(r["position"]),
            },
        )
    print(f"channels: upserted {len(rows)} rows")


def resolve_member_collisions(rows: list[dict]) -> tuple[list[dict], dict]:
    """Rename colliding (user_id, server_id) members and return a lookup used
    to re-key messages: (orig_user_id, server_id) -> list of
    {resolved_user_id, join_date, last_active} sorted by join_date asc.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        groups.setdefault((r["user_id"], r["server_id"]), []).append(r)

    resolved_rows = []
    collision_lookup: dict[tuple[str, str], list[dict]] = {}
    renamed = 0

    for (user_id, server_id), members in groups.items():
        if len(members) == 1:
            resolved_rows.append(members[0])
            continue

        members_sorted = sorted(members, key=lambda r: parse_ts(r["join_date"]))
        entries = []
        for i, m in enumerate(members_sorted):
            resolved_id = user_id if i == 0 else f"{user_id}_{i + 1}"
            if i > 0:
                renamed += 1
            m = dict(m)
            m["user_id"] = resolved_id
            resolved_rows.append(m)
            entries.append(
                {
                    "resolved_user_id": resolved_id,
                    "join_date": parse_ts(m["join_date"]),
                    "last_active": parse_ts(m["last_active"]),
                }
            )
        collision_lookup[(user_id, server_id)] = entries

    print(f"members: resolved {len(collision_lookup)} colliding user_id groups, renamed {renamed} rows")
    return resolved_rows, collision_lookup


def load_members(cur, rows: list[dict]) -> None:
    stmt = sql.SQL(
        """
        INSERT INTO members (
            user_id, server_id, username, display_name, discriminator,
            avatar_hash, is_bot, join_date, last_active, roles,
            messages_sent, voice_minutes, is_owner
        ) VALUES (
            %(user_id)s, %(server_id)s, %(username)s, %(display_name)s, %(discriminator)s,
            %(avatar_hash)s, %(is_bot)s, %(join_date)s, %(last_active)s, %(roles)s,
            %(messages_sent)s, %(voice_minutes)s, %(is_owner)s
        )
        ON CONFLICT (user_id, server_id) DO UPDATE SET
            username = EXCLUDED.username,
            display_name = EXCLUDED.display_name,
            discriminator = EXCLUDED.discriminator,
            avatar_hash = EXCLUDED.avatar_hash,
            is_bot = EXCLUDED.is_bot,
            join_date = EXCLUDED.join_date,
            last_active = EXCLUDED.last_active,
            roles = EXCLUDED.roles,
            messages_sent = EXCLUDED.messages_sent,
            voice_minutes = EXCLUDED.voice_minutes,
            is_owner = EXCLUDED.is_owner
        """
    )
    for r in rows:
        roles = [x for x in r["roles"].split(",") if x] if r["roles"] else []
        cur.execute(
            stmt,
            {
                "user_id": r["user_id"],
                "server_id": r["server_id"],
                "username": r["username"],
                "display_name": r["display_name"],
                "discriminator": r["discriminator"],
                "avatar_hash": r["avatar_hash"] or None,
                "is_bot": parse_bool(r["is_bot"]),
                "join_date": parse_ts(r["join_date"]),
                "last_active": parse_ts(r["last_active"]),
                "roles": roles,
                "messages_sent": parse_int(r["messages_sent"]),
                "voice_minutes": parse_int(r["voice_minutes"]),
                "is_owner": parse_bool(r["is_owner"]),
            },
        )
    print(f"members: upserted {len(rows)} rows")


def resolve_message_attribution(rows: list[dict], collision_lookup: dict) -> list[dict]:
    resolved = []
    dropped = 0
    window_unique = 0
    nearest_fallback = 0

    for r in rows:
        key = (r["user_id"], r["server_id"])
        entries = collision_lookup.get(key)
        if entries is None:
            resolved.append(r)
            continue

        ts = parse_ts(r["timestamp"])
        inside = [e for e in entries if e["join_date"] <= ts <= e["last_active"]]

        if len(inside) == 1:
            chosen = inside[0]
            window_unique += 1
        elif len(inside) >= 2: #dropping because a single message is for both users
            dropped += 1
            continue
        else: # if outside of both user windows, pick the nearest one by distance to the window
            def distance(e):
                if ts < e["join_date"]:
                    return e["join_date"] - ts
                return ts - e["last_active"]

            chosen = min(entries, key=distance)
            nearest_fallback += 1

        r = dict(r)
        r["user_id"] = chosen["resolved_user_id"]
        resolved.append(r)

    print(
        f"messages: {window_unique} attributed by unique window match, "
        f"{nearest_fallback} attributed by nearest-window fallback, "
        f"{dropped} dropped as unresolvably ambiguous"
    )
    return resolved


def load_messages(cur, rows: list[dict]) -> None:
    stmt = sql.SQL(
        """
        INSERT INTO messages (
            message_id, server_id, channel_id, user_id, "timestamp", content,
            has_attachment, has_embed, reaction_count, is_pinned, length
        ) VALUES (
            %(message_id)s, %(server_id)s, %(channel_id)s, %(user_id)s, %(timestamp)s, %(content)s,
            %(has_attachment)s, %(has_embed)s, %(reaction_count)s, %(is_pinned)s, %(length)s
        )
        ON CONFLICT (message_id) DO UPDATE SET
            server_id = EXCLUDED.server_id,
            channel_id = EXCLUDED.channel_id,
            user_id = EXCLUDED.user_id,
            "timestamp" = EXCLUDED."timestamp",
            content = EXCLUDED.content,
            has_attachment = EXCLUDED.has_attachment,
            has_embed = EXCLUDED.has_embed,
            reaction_count = EXCLUDED.reaction_count,
            is_pinned = EXCLUDED.is_pinned,
            length = EXCLUDED.length
        """
    )
    for r in rows:
        cur.execute(
            stmt,
            {
                "message_id": r["message_id"],
                "server_id": r["server_id"],
                "channel_id": r["channel_id"],
                "user_id": r["user_id"],
                "timestamp": parse_ts(r["timestamp"]),
                "content": r["content"],
                "has_attachment": parse_bool(r["has_attachment"]),
                "has_embed": parse_bool(r["has_embed"]),
                "reaction_count": parse_int(r["reaction_count"]),
                "is_pinned": parse_bool(r["is_pinned"]),
                "length": parse_int(r["length"]),
            },
        )
    print(f"messages: upserted {len(rows)} rows")


def load_daily_stats(cur, rows: list[dict]) -> None:
    stmt = sql.SQL(
        """
        INSERT INTO daily_stats (
            server_id, date, total_messages, new_members, active_members,
            total_members, day_of_week, is_weekend
        ) VALUES (
            %(server_id)s, %(date)s, %(total_messages)s, %(new_members)s, %(active_members)s,
            %(total_members)s, %(day_of_week)s, %(is_weekend)s
        )
        ON CONFLICT (server_id, date) DO UPDATE SET
            total_messages = EXCLUDED.total_messages,
            new_members = EXCLUDED.new_members,
            active_members = EXCLUDED.active_members,
            total_members = EXCLUDED.total_members,
            day_of_week = EXCLUDED.day_of_week,
            is_weekend = EXCLUDED.is_weekend
        """
    )
    for r in rows:
        cur.execute(
            stmt,
            {
                "server_id": r["server_id"],
                "date": r["date"],
                "total_messages": parse_int(r["total_messages"]),
                "new_members": parse_int(r["new_members"]),
                "active_members": parse_int(r["active_members"]),
                "total_members": parse_int(r["total_members"]),
                "day_of_week": parse_int(r["day_of_week"]),
                "is_weekend": r["is_weekend"] in ("1", "True", "true"),
            },
        )
    print(f"daily_stats: upserted {len(rows)} rows")


def load_channel_daily_stats(cur, rows: list[dict]) -> None:
    stmt = sql.SQL(
        """
        INSERT INTO channel_daily_stats (
            channel_id, server_id, date, message_count, active_users
        ) VALUES (
            %(channel_id)s, %(server_id)s, %(date)s, %(message_count)s, %(active_users)s
        )
        ON CONFLICT (channel_id, date) DO UPDATE SET
            server_id = EXCLUDED.server_id,
            message_count = EXCLUDED.message_count,
            active_users = EXCLUDED.active_users
        """
    )
    for r in rows:
        cur.execute(
            stmt,
            {
                "channel_id": r["channel_id"],
                "server_id": r["server_id"],
                "date": r["date"],
                "message_count": parse_int(r["message_count"]),
                "active_users": parse_int(r["active_users"]),
            },
        )
    print(f"channel_daily_stats: upserted {len(rows)} rows")


def main() -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL environment variable is required")

    schema_sql = (Path(__file__).resolve().parent / "schema.sql").read_text()

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(schema_sql)
        conn.commit()

        with conn.cursor() as cur:
            load_servers(cur, read_csv("servers.csv"))
            load_channels(cur, read_csv("channels.csv"))

            member_rows, collision_lookup = resolve_member_collisions(read_csv("members.csv"))
            load_members(cur, member_rows)

            message_rows = resolve_message_attribution(read_csv("messages_sample.csv"), collision_lookup)
            load_messages(cur, message_rows)

            load_daily_stats(cur, read_csv("daily_stats.csv"))
            load_channel_daily_stats(cur, read_csv("channel_daily_stats.csv"))
        conn.commit()

    print("load complete")


if __name__ == "__main__":
    main()
