"""End-to-end /pins tests against the generic artifact model: pin creation
reads out of real chat history (created via /chat with the real
query+chart plugins, scripted provider standing in for the LLM), then
exercises list/reorder/unpin/refresh/download against the real seeded test
DB. Covers both a chart pin (chain = [query, chart]) and a bare query pin
(chain = [query] alone) -- the whole point of this generalization is that
pinning isn't chart-specific anymore.
"""

import pytest

from app.agent.loop import AgentLoop
from app.agent.messages import AssistantTurn, ToolCall
from app.agent.service import ChatService
from app.main import app

from tests.agent.fakes import ScriptedProvider


def _use_provider(turns):
    app.state.chat_service = ChatService(loop=AgentLoop(provider=ScriptedProvider(turns), max_tool_retries=2))


async def _chat_query_only(client, sql="SELECT server_id, approximate_member_count FROM servers ORDER BY server_id"):
    """Drives a single query turn, returns (session_id, query_tool_call_id)."""
    _use_provider(
        [
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c1", name="query", arguments={"sql": sql})]),
            AssistantTurn(text="Here you go.", tool_calls=[]),
        ]
    )
    resp = await client.post("/chat", json={"message": "look this up"})
    body = resp.json()
    query_call = next(tc for tc in body["tool_calls"] if tc["name"] == "query")
    return body["session_id"], query_call["tool_call_id"]


async def _chat_query_then_chart(client, title="Members per server"):
    """Drives a query -> chart turn, returns (session_id, chart_tool_call_id)."""
    _use_provider(
        [
            AssistantTurn(
                text=None,
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="query",
                        arguments={"sql": "SELECT server_id, approximate_member_count FROM servers ORDER BY server_id"},
                    )
                ],
            ),
            AssistantTurn(
                text=None,
                tool_calls=[
                    ToolCall(
                        id="c2",
                        name="chart",
                        arguments={
                            "source_call_id": "c1",
                            "chart_type": "bar",
                            "title": title,
                            "x_field": "server_id",
                            "y_field": "approximate_member_count",
                        },
                    )
                ],
            ),
            AssistantTurn(text="Here's your chart.", tool_calls=[]),
        ]
    )
    resp = await client.post("/chat", json={"message": "chart member counts per server"})
    body = resp.json()
    chart_call = next(tc for tc in body["tool_calls"] if tc["name"] == "chart")
    return body["session_id"], chart_call["tool_call_id"]


@pytest.mark.asyncio
async def test_pin_a_chart_call_chain_has_two_steps(client):
    session_id, chart_tool_call_id = await _chat_query_then_chart(client)

    resp = await client.post("/pins", json={"session_id": session_id, "tool_call_id": chart_tool_call_id})

    assert resp.status_code == 201
    body = resp.json()
    assert body["plugin_name"] == "chart"
    assert body["display_kind"] == "chart"
    assert body["title"] == "Members per server"
    assert [step["plugin_name"] for step in body["call_chain"]] == ["query", "chart"]
    assert {r["server_id"] for r in body["cached_data"]["data"]} == {"srv_1", "srv_2"}
    assert body["position"] == 0


@pytest.mark.asyncio
async def test_pin_a_bare_query_call_chain_has_one_step(client):
    """The whole point of the generalization: a plain query result is
    pinnable on its own, not just chart results.
    """
    session_id, query_tool_call_id = await _chat_query_only(client)

    resp = await client.post("/pins", json={"session_id": session_id, "tool_call_id": query_tool_call_id})

    assert resp.status_code == 201
    body = resp.json()
    assert body["plugin_name"] == "query"
    assert body["display_kind"] == "table"
    assert body["title"] == "query result"  # no `title` argument on a bare query call
    assert [step["plugin_name"] for step in body["call_chain"]] == ["query"]
    assert {r["server_id"] for r in body["cached_data"]["rows"]} == {"srv_1", "srv_2"}


@pytest.mark.asyncio
async def test_pin_unknown_session_is_404(client):
    resp = await client.post(
        "/pins", json={"session_id": "00000000-0000-0000-0000-000000000000", "tool_call_id": "c2"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pin_unknown_tool_call_id_is_404(client):
    session_id, _ = await _chat_query_then_chart(client)
    resp = await client.post("/pins", json={"session_id": session_id, "tool_call_id": "does-not-exist"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pin_a_failed_call_is_400(client):
    _use_provider(
        [
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c1", name="query", arguments={"sql": "not valid sql ((("})]),
            AssistantTurn(text="that failed", tool_calls=[]),
        ]
    )
    resp = await client.post("/chat", json={"message": "run bad sql"})
    session_id = resp.json()["session_id"]

    pin_resp = await client.post("/pins", json={"session_id": session_id, "tool_call_id": "c1"})
    assert pin_resp.status_code == 400


@pytest.mark.asyncio
async def test_list_pins_ordered_by_position(client):
    session_1, chart_1 = await _chat_query_then_chart(client, title="First")
    session_2, chart_2 = await _chat_query_then_chart(client, title="Second")
    await client.post("/pins", json={"session_id": session_1, "tool_call_id": chart_1})
    await client.post("/pins", json={"session_id": session_2, "tool_call_id": chart_2})

    resp = await client.get("/pins")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [p["title"] for p in items] == ["First", "Second"]
    assert [p["position"] for p in items] == [0, 1]


@pytest.mark.asyncio
async def test_unpin_removes_it(client):
    session_id, chart_tool_call_id = await _chat_query_then_chart(client)
    pin = (await client.post("/pins", json={"session_id": session_id, "tool_call_id": chart_tool_call_id})).json()

    del_resp = await client.delete(f"/pins/{pin['pin_id']}")
    assert del_resp.status_code == 204

    list_resp = await client.get("/pins")
    assert list_resp.json()["items"] == []


@pytest.mark.asyncio
async def test_unpin_unknown_id_is_404(client):
    resp = await client.delete("/pins/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reorder_pins(client):
    session_id, chart_1 = await _chat_query_then_chart(client, title="First")
    _, chart_2 = await _chat_query_then_chart(client, title="Second")
    pin_a = (await client.post("/pins", json={"session_id": session_id, "tool_call_id": chart_1})).json()
    pin_b = (await client.post("/pins", json={"session_id": session_id, "tool_call_id": chart_2})).json()

    resp = await client.put("/pins/order", json={"order": [pin_b["pin_id"], pin_a["pin_id"]]})

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [p["pin_id"] for p in items] == [pin_b["pin_id"], pin_a["pin_id"]]
    assert [p["position"] for p in items] == [0, 1]


@pytest.mark.asyncio
async def test_reorder_with_missing_pin_id_is_400(client):
    session_id, chart_tool_call_id = await _chat_query_then_chart(client)
    await client.post("/pins", json={"session_id": session_id, "tool_call_id": chart_tool_call_id})

    resp = await client.put("/pins/order", json={"order": ["00000000-0000-0000-0000-000000000000"]})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_refresh_chart_pin_replays_the_whole_chain(client):
    session_id, chart_tool_call_id = await _chat_query_then_chart(client)
    pin = (await client.post("/pins", json={"session_id": session_id, "tool_call_id": chart_tool_call_id})).json()

    resp = await client.post(f"/pins/{pin['pin_id']}/refresh")

    assert resp.status_code == 200
    body = resp.json()
    assert {r["server_id"] for r in body["cached_data"]["data"]} == {"srv_1", "srv_2"}


@pytest.mark.asyncio
async def test_refresh_query_only_pin(client):
    session_id, query_tool_call_id = await _chat_query_only(client)
    pin = (await client.post("/pins", json={"session_id": session_id, "tool_call_id": query_tool_call_id})).json()

    resp = await client.post(f"/pins/{pin['pin_id']}/refresh")

    assert resp.status_code == 200
    assert {r["server_id"] for r in resp.json()["cached_data"]["rows"]} == {"srv_1", "srv_2"}


@pytest.mark.asyncio
async def test_refresh_unknown_pin_is_404(client):
    resp = await client.post("/pins/00000000-0000-0000-0000-000000000000/refresh")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_download_query_pin_is_csv(client):
    session_id, query_tool_call_id = await _chat_query_only(client)
    pin = (await client.post("/pins", json={"session_id": session_id, "tool_call_id": query_tool_call_id})).json()

    resp = await client.get(f"/pins/{pin['pin_id']}/download")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    assert "srv_1" in resp.text
    assert "srv_2" in resp.text


@pytest.mark.asyncio
async def test_download_chart_pin_is_400_not_downloadable(client):
    """chart has no to_file() override -- image export is a frontend
    concern, not a backend one (see README Pinning).
    """
    session_id, chart_tool_call_id = await _chat_query_then_chart(client)
    pin = (await client.post("/pins", json={"session_id": session_id, "tool_call_id": chart_tool_call_id})).json()

    resp = await client.get(f"/pins/{pin['pin_id']}/download")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_download_unknown_pin_is_404(client):
    resp = await client.get("/pins/00000000-0000-0000-0000-000000000000/download")
    assert resp.status_code == 404
