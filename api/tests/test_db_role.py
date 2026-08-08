import asyncpg
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def api_role_conn(api_role_url):
    conn = await asyncpg.connect(api_role_url)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_api_role_can_select(api_role_conn):
    # Sanity check the role isn't *over*-restricted either -- SELECT must work.
    await api_role_conn.fetchval("SELECT COUNT(*) FROM servers")


@pytest.mark.asyncio
async def test_api_role_cannot_insert(api_role_conn):
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await api_role_conn.execute(
            "INSERT INTO servers (server_id, server_name, owner_id, creation_date, region, "
            "verification_level, default_message_notifications, explicit_content_filter, "
            "widget_enabled, premium_tier, premium_subscription_count, approximate_member_count, "
            "approximate_presence_count) VALUES ('x', 'x', 'x', now(), 'us-east', 0, 0, 0, true, 0, 0, 0, 0)"
        )


@pytest.mark.asyncio
async def test_api_role_cannot_update(api_role_conn):
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await api_role_conn.execute("UPDATE servers SET server_name = 'x'")


@pytest.mark.asyncio
async def test_api_role_cannot_delete(api_role_conn):
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await api_role_conn.execute("DELETE FROM servers")


@pytest.mark.asyncio
async def test_api_role_cannot_drop_table(api_role_conn):
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await api_role_conn.execute("DROP TABLE servers")


@pytest.mark.asyncio
async def test_api_role_cannot_create_table(api_role_conn):
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await api_role_conn.execute("CREATE TABLE evil (id INT)")
