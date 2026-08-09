"""Verifies AGENT_DB_USER's grants are what Part 3's Safety section claims:
read-only on domain tables (the role that executes untrusted LLM-generated
SQL must not be able to write there no matter what), read/write only on its
own chat_sessions/chat_messages/pinned_artifacts. Connects directly as the
role and attempts writes, rather than trusting the app layer to never issue
one -- this is the only restricted role in the system (the API itself
connects with the same role the loader uses; see README/docker-compose.yml
for why a second least-privilege role there was cut as unnecessary).
"""

import asyncpg
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def agent_role_conn(agent_role_url):
    conn = await asyncpg.connect(agent_role_url)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_agent_role_can_select_domain_tables(agent_role_conn):
    await agent_role_conn.fetchval("SELECT COUNT(*) FROM servers")
    await agent_role_conn.fetchval("SELECT COUNT(*) FROM messages")


@pytest.mark.asyncio
async def test_agent_role_cannot_insert_into_domain_table(agent_role_conn):
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await agent_role_conn.execute(
            "INSERT INTO servers (server_id, server_name, owner_id, creation_date, region, "
            "verification_level, default_message_notifications, explicit_content_filter, "
            "widget_enabled, premium_tier, premium_subscription_count, approximate_member_count, "
            "approximate_presence_count) VALUES ('x', 'x', 'x', now(), 'us-east', 0, 0, 0, true, 0, 0, 0, 0)"
        )


@pytest.mark.asyncio
async def test_agent_role_cannot_update_domain_table(agent_role_conn):
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await agent_role_conn.execute("UPDATE servers SET server_name = 'x'")


@pytest.mark.asyncio
async def test_agent_role_cannot_delete_from_domain_table(agent_role_conn):
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await agent_role_conn.execute("DELETE FROM messages")


@pytest.mark.asyncio
async def test_agent_role_cannot_drop_domain_table(agent_role_conn):
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await agent_role_conn.execute("DROP TABLE servers")


@pytest.mark.asyncio
async def test_agent_role_cannot_create_table(agent_role_conn):
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await agent_role_conn.execute("CREATE TABLE evil (id INT)")


@pytest.mark.asyncio
async def test_agent_role_can_write_its_own_chat_tables(agent_role_conn):
    session_id = await agent_role_conn.fetchval("INSERT INTO chat_sessions DEFAULT VALUES RETURNING session_id")
    await agent_role_conn.execute(
        "INSERT INTO chat_messages (session_id, role, content) VALUES ($1, 'user', 'hi')", session_id
    )


@pytest.mark.asyncio
async def test_agent_role_can_write_pinned_artifacts(agent_role_conn):
    session_id = await agent_role_conn.fetchval("INSERT INTO chat_sessions DEFAULT VALUES RETURNING session_id")
    await agent_role_conn.execute(
        """
        INSERT INTO pinned_artifacts (
            session_id, source_tool_call_id, plugin_name, display_kind, title,
            call_chain, cached_data, "position"
        ) VALUES ($1, 'c1', 'query', 'table', 'test', '[]'::jsonb, '[]'::jsonb, 0)
        """,
        session_id,
    )
