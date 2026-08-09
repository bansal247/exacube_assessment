"""QueryPlugin against a real Postgres, connected via the same read-only
agent role production uses (see conftest.py's provision_agent_role) -- not
just that it returns rows, but that the role's grants are actually
sufficient for SELECT to work end to end.
"""

import asyncpg
import pytest

from app.agent.plugins.base import PluginContext, PluginError
from app.agent.plugins.query import QueryPlugin


@pytest.fixture
async def agent_conn(agent_role_url):
    conn = await asyncpg.connect(agent_role_url)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_query_returns_rows(agent_conn, seeded_admin_conn):
    plugin = QueryPlugin()
    result = await plugin.execute(
        {"sql": "SELECT server_id, server_name FROM servers ORDER BY server_id"},
        PluginContext(agent_conn=agent_conn),
    )
    assert result.data["row_count"] == 2
    assert result.data["rows"][0]["server_id"] == "srv_1"


@pytest.mark.asyncio
async def test_query_missing_sql_argument_raises_plugin_error(agent_conn):
    plugin = QueryPlugin()
    with pytest.raises(PluginError):
        await plugin.execute({}, PluginContext(agent_conn=agent_conn))


@pytest.mark.asyncio
async def test_query_no_connection_in_context_raises_plugin_error(agent_conn):
    plugin = QueryPlugin()
    with pytest.raises(PluginError):
        await plugin.execute({"sql": "SELECT 1"}, PluginContext(agent_conn=None))


@pytest.mark.asyncio
async def test_query_invalid_sql_raises_plugin_error_not_a_crash(agent_conn):
    plugin = QueryPlugin()
    with pytest.raises(PluginError):
        await plugin.execute({"sql": "SELECT this is not valid sql"}, PluginContext(agent_conn=agent_conn))


@pytest.mark.asyncio
async def test_query_datetime_values_are_json_safe(agent_conn, seeded_admin_conn):
    plugin = QueryPlugin()
    result = await plugin.execute(
        {"sql": "SELECT creation_date FROM servers LIMIT 1"}, PluginContext(agent_conn=agent_conn)
    )
    # to_jsonable must have converted the asyncpg datetime to an ISO string
    # -- a raw datetime would fail json.dumps in the agent loop.
    assert isinstance(result.data["rows"][0]["creation_date"], str)


@pytest.mark.asyncio
async def test_query_rejects_semicolon_injected_drop_and_table_survives(agent_conn, seeded_admin_conn):
    """The brief's own example of what string-matching would miss --
    proven end to end, not just that sql_safety rejects it in isolation:
    the plugin never even attempts to execute it, and the table is
    provably untouched afterward.
    """
    plugin = QueryPlugin()
    with pytest.raises(PluginError):
        await plugin.execute({"sql": "SELECT 1; DROP TABLE servers"}, PluginContext(agent_conn=agent_conn))

    survives = await plugin.execute(
        {"sql": "SELECT COUNT(*) AS n FROM servers"}, PluginContext(agent_conn=agent_conn)
    )
    assert survives.data["rows"][0]["n"] == 2


@pytest.mark.asyncio
async def test_query_rejects_top_level_write(agent_conn):
    plugin = QueryPlugin()
    with pytest.raises(PluginError):
        await plugin.execute({"sql": "DROP TABLE servers"}, PluginContext(agent_conn=agent_conn))


@pytest.mark.asyncio
async def test_query_adds_limit_when_missing(agent_conn, seeded_admin_conn):
    plugin = QueryPlugin()
    result = await plugin.execute({"sql": "SELECT * FROM servers"}, PluginContext(agent_conn=agent_conn))
    assert "LIMIT" in result.data["sql"].upper()


def test_query_declares_table_display_kind():
    assert QueryPlugin.display_kind == "table"


@pytest.mark.asyncio
async def test_query_to_file_renders_csv():
    plugin = QueryPlugin()
    data = {"sql": "SELECT 1", "row_count": 2, "rows": [{"a": "1", "b": "x"}, {"a": "2", "b": "y"}]}

    file = await plugin.to_file(data)

    assert file is not None
    assert file.filename == "query_result.csv"
    assert file.content_type == "text/csv"
    text = file.content.decode("utf-8")
    assert "a,b" in text
    assert "1,x" in text
    assert "2,y" in text


@pytest.mark.asyncio
async def test_query_to_file_handles_empty_rows():
    plugin = QueryPlugin()
    file = await plugin.to_file({"sql": "SELECT 1", "row_count": 0, "rows": []})
    assert file is not None
    assert file.content == b""
