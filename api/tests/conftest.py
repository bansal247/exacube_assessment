import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from testcontainers.postgres import PostgresContainer

## Need for config hence setting here -- Settings() is evaluated at import
## time, so every *_url/*_key it requires needs a placeholder before the
## first `from app... import` below. Tests never use these values directly;
## real per-test connections are wired in explicitly (dependency_overrides,
## or a directly-constructed connection/pool passed straight to the code
## under test) against whatever testcontainers actually started.
os.environ.setdefault("DATABASE_URL", "postgresql://placeholder:placeholder@localhost/placeholder")
os.environ.setdefault("AGENT_DATABASE_URL", "postgresql://placeholder:placeholder@localhost/placeholder")
os.environ.setdefault("LLM_PROVIDER", "openai")
os.environ.setdefault("OPENAI_API_KEY", "placeholder")

from app.agent.loop import AgentLoop
from app.agent.plugins.registry import discover_plugins
from app.agent.service import ChatService
from app.main import app
from app.routers.deps import get_agent_connection, get_connection

from tests.agent.fakes import ScriptedProvider

# main.py's lifespan normally calls this, but httpx's ASGITransport never
# fires ASGI lifespan events, so the `client` fixture doesn't get it for
# free -- tests that route through /chat need the registry populated to
# resolve tool names like "query".
discover_plugins()

SCHEMA_SQL = (Path(__file__).resolve().parent.parent.parent / "db" / "schema.sql").read_text()
NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
TABLES = "servers, channels, members, messages, daily_stats, channel_daily_stats, chat_sessions"
# chat_sessions is included (not chat_messages/pinned_artifacts directly) because
# both cascade from it via ON DELETE CASCADE FKs, and TRUNCATE ... CASCADE
# (used everywhere this constant appears) follows that automatically.
AGENT_ROLE = "test_agent_role"
AGENT_ROLE_PASSWORD = "test_agent_role_password"

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
async def provision_agent_role(postgres_url, apply_schema):
    # Mirrors db/load.py's provision_agent_role: read-only on domain tables,
    # read/write only on the agent's own chat history + pinned_artifacts tables.
    conn = await asyncpg.connect(postgres_url)
    try:
        await conn.execute(f"CREATE ROLE {AGENT_ROLE} WITH LOGIN PASSWORD '{AGENT_ROLE_PASSWORD}'")
        await conn.execute(f"GRANT USAGE ON SCHEMA public TO {AGENT_ROLE}")
        await conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {AGENT_ROLE}")
        await conn.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {AGENT_ROLE}")
        await conn.execute(f"GRANT SELECT, INSERT, UPDATE ON chat_sessions, chat_messages TO {AGENT_ROLE}")
        await conn.execute(f"GRANT USAGE ON SEQUENCE chat_messages_message_id_seq TO {AGENT_ROLE}")
        await conn.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON pinned_artifacts TO {AGENT_ROLE}")
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def agent_role_url(postgres_url, provision_agent_role):
    _, rest = postgres_url.split("://", 1)
    _, host_and_db = rest.split("@", 1)
    return f"postgresql://{AGENT_ROLE}:{AGENT_ROLE_PASSWORD}@{host_and_db}"


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
async def seeded_admin_conn(postgres_url):
    """For tests that call a plugin/repository directly (no router, no
    /chat), bypassing `client`'s dependency-override wiring entirely.
    """
    conn = await asyncpg.connect(postgres_url)
    try:
        await conn.execute(f"TRUNCATE {TABLES} CASCADE")
        await seed(conn)
        yield conn
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def client(postgres_url, agent_role_url):
    # API connects with the same (unrestricted-within-the-DB) role as the
    # loader now -- see docker-compose.yml/README for why a separate
    # least-privilege role for the API specifically was cut. admin_pool
    # backs both seeding/truncation *and* the get_connection override.
    admin_pool = await asyncpg.create_pool(postgres_url, min_size=1, max_size=5)
    agent_pool = await asyncpg.create_pool(agent_role_url, min_size=1, max_size=5)
    async with admin_pool.acquire() as conn:
        await conn.execute(f"TRUNCATE {TABLES} CASCADE")
        await seed(conn)

    async def override_get_connection():
        async with admin_pool.acquire() as conn:
            yield conn

    async def override_get_agent_connection():
        async with agent_pool.acquire() as conn:
            yield conn

    app.dependency_overrides[get_connection] = override_get_connection
    app.dependency_overrides[get_agent_connection] = override_get_agent_connection
    # Default chat_service is a fresh empty-scripted provider -- individual
    # /chat tests set app.state.chat_service.loop._provider._turns before
    # calling, or replace app.state.chat_service outright for scenarios
    # that need multiple scripted turns. This just guarantees the app
    # always has *a* chat_service, matching what main.py's lifespan sets in
    # production (there, an AnthropicProvider instead of ScriptedProvider).
    app.state.chat_service = ChatService(loop=AgentLoop(provider=ScriptedProvider([]), max_tool_retries=2))
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_connection, None)
        app.dependency_overrides.pop(get_agent_connection, None)
        await admin_pool.close()
        await agent_pool.close()
