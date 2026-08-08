import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from testcontainers.postgres import PostgresContainer

## Need for config hence setting here
os.environ.setdefault("DATABASE_URL", "postgresql://placeholder:placeholder@localhost/placeholder")

from app.main import app
from app.routers.deps import get_connection

SCHEMA_SQL = (Path(__file__).resolve().parent.parent.parent / "db" / "schema.sql").read_text()
NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
TABLES = "servers, channels, members, messages, daily_stats, channel_daily_stats"
API_ROLE = "test_api_role"
API_ROLE_PASSWORD = "test_api_role_password"

@pytest.fixture(scope="session")
def postgres_url():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg.get_connection_url(driver="asyncpg").replace("+asyncpg", "")


@pytest_asyncio.fixture(scope="session", autouse=True)
async def apply_schema(postgres_url):
    conn = await asyncpg.connect(postgres_url)
    try:
        await conn.execute(SCHEMA_SQL)
    finally:
        await conn.close()

@pytest_asyncio.fixture(scope="session", autouse=True)
async def provision_api_role(postgres_url, apply_schema):
    conn = await asyncpg.connect(postgres_url)
    try:
        await conn.execute(f"CREATE ROLE {API_ROLE} WITH LOGIN PASSWORD '{API_ROLE_PASSWORD}'")
        await conn.execute(f"GRANT USAGE ON SCHEMA public TO {API_ROLE}")
        await conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {API_ROLE}")
        await conn.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {API_ROLE}")
    finally:
        await conn.close()

@pytest.fixture(scope="session")
def api_role_url(postgres_url, provision_api_role):
    _, rest = postgres_url.split("://", 1)
    _, host_and_db = rest.split("@", 1)
    return f"postgresql://{API_ROLE}:{API_ROLE_PASSWORD}@{host_and_db}"

async def seed(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        INSERT INTO servers (
            server_id, server_name, owner_id, creation_date, region,
            verification_level, default_message_notifications, explicit_content_filter,
            widget_enabled, premium_tier, premium_subscription_count,
            approximate_member_count, approximate_presence_count
        ) VALUES
        ('srv_1', 'Test Server 1', 'owner_1', $1, 'us-east', 0, 0, 0, true, 0, 0, 100, 10),
        ('srv_2', 'Test Server 2', 'owner_2', $1, 'us-west', 0, 0, 0, true, 0, 0, 50, 5)
        """,
        NOW - timedelta(days=100),
    )
    await conn.execute(
        """
        INSERT INTO channels (channel_id, server_id, channel_name, channel_type, nsfw, rate_limit_per_user, "position")
        VALUES
        ('chan_1', 'srv_1', 'general', 'text', false, 0, 0),
        ('chan_2', 'srv_1', 'random', 'text', false, 0, 1),
        ('chan_voice_1', 'srv_1', 'Lounge', 'voice', false, 0, 2)
        """
    )
    await conn.execute(
        """
        INSERT INTO members (
            user_id, server_id, username, display_name, discriminator, is_bot,
            join_date, last_active, roles, messages_sent, voice_minutes, is_owner
        ) VALUES
        ('user_1', 'srv_1', 'alice', 'Alice', '0001', false, $1, $2, '{}', 500, 10, false),
        ('user_2', 'srv_1', 'bob', 'Bob', '0002', false, $1, $2, '{}', 100, 5, false),
        ('user_3', 'srv_1', 'carol', 'Carol', '0003', false, $1, $2, '{admin}', 900, 20, true)
        """,
        NOW - timedelta(days=90),
        NOW,
    )
    await conn.execute(
        """
        INSERT INTO messages (
            message_id, server_id, channel_id, user_id, "timestamp", content,
            has_attachment, has_embed, reaction_count, is_pinned, length
        ) VALUES
        ('msg_1', 'srv_1', 'chan_1', 'user_1', $1, 'hello', false, false, 0, false, 5),
        ('msg_2', 'srv_1', 'chan_1', 'user_2', $2, 'hi there', false, false, 1, false, 8)
        """,
        NOW - timedelta(hours=2),
        NOW - timedelta(hours=1),
    )
    await conn.execute(
        """
        INSERT INTO daily_stats (server_id, date, total_messages, new_members, active_members, total_members, day_of_week, is_weekend)
        VALUES ('srv_1', $1, 42, 1, 3, 100, 3, false)
        """,
        NOW.date(),
    )
    await conn.execute(
        """
        INSERT INTO channel_daily_stats (channel_id, server_id, date, message_count, active_users)
        VALUES ('chan_1', 'srv_1', $1, 30, 2)
        """,
        NOW.date(),
    )

@pytest_asyncio.fixture
async def client(postgres_url, api_role_url):
    admin_pool = await asyncpg.create_pool(postgres_url, min_size=1, max_size=5)
    api_pool = await asyncpg.create_pool(api_role_url, min_size=1, max_size=5)
    async with admin_pool.acquire() as conn:
        await conn.execute(f"TRUNCATE {TABLES} CASCADE")
        await seed(conn)

    async def override_get_connection():
        async with api_pool.acquire() as conn:
            yield conn

    app.dependency_overrides[get_connection] = override_get_connection
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_connection, None)
        await admin_pool.close()
        await api_pool.close()
