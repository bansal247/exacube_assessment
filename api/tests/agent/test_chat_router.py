"""End-to-end /chat tests through the real HTTP layer (routing, Pydantic
validation, session persistence) with a scripted provider standing in for
the LLM -- no network call, no Anthropic key needed to run this suite.
"""

import pytest

from app.agent.loop import AgentLoop
from app.agent.messages import AssistantTurn, ToolCall
from app.agent.service import ChatService
from app.main import app

from tests.agent.fakes import ScriptedProvider


def _use_provider(turns):
    app.state.chat_service = ChatService(loop=AgentLoop(provider=ScriptedProvider(turns), max_tool_retries=2))


@pytest.mark.asyncio
async def test_chat_happy_path_creates_session_and_answers(client):
    _use_provider(
        [
            AssistantTurn(
                text=None,
                tool_calls=[ToolCall(id="c1", name="query", arguments={"sql": "SELECT COUNT(*) FROM servers"})],
            ),
            AssistantTurn(text="There are 2 servers.", tool_calls=[]),
        ]
    )

    resp = await client.post("/chat", json={"message": "how many servers are there?"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "There are 2 servers."
    assert body["session_id"]
    assert len(body["tool_calls"]) == 1
    assert body["tool_calls"][0]["name"] == "query"
    assert body["tool_calls"][0]["is_error"] is False


@pytest.mark.asyncio
async def test_chat_decline_with_no_tool_call(client):
    _use_provider([AssistantTurn(text="I can't answer that from this dataset.", tool_calls=[])])

    resp = await client.post("/chat", json={"message": "what's the weather in Tokyo?"})

    assert resp.status_code == 200
    assert resp.json()["reply"] == "I can't answer that from this dataset."
    assert resp.json()["tool_calls"] == []


@pytest.mark.asyncio
async def test_chat_session_continuity_reuses_history(client):
    _use_provider([AssistantTurn(text="first reply", tool_calls=[])])
    first = await client.post("/chat", json={"message": "first message"})
    session_id = first.json()["session_id"]

    _use_provider([AssistantTurn(text="second reply", tool_calls=[])])
    second = await client.post("/chat", json={"session_id": session_id, "message": "second message"})

    assert second.status_code == 200
    assert second.json()["session_id"] == session_id
    assert second.json()["reply"] == "second reply"


@pytest.mark.asyncio
async def test_chat_unknown_session_id_is_404(client):
    _use_provider([AssistantTurn(text="unreachable", tool_calls=[])])
    resp = await client.post(
        "/chat", json={"session_id": "00000000-0000-0000-0000-000000000000", "message": "hi"}
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_chat_empty_message_is_422(client):
    resp = await client.post("/chat", json={"message": ""})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_chat_invalid_session_id_format_is_422(client):
    resp = await client.post("/chat", json={"session_id": "not-a-uuid", "message": "hi"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_chat_query_then_chart_chains_across_two_rounds(client):
    """End-to-end with the *real* registered query and chart plugins (not
    fakes) against the seeded test DB -- proves the registry, the
    `consumes` validation, and the turn-scoped PluginContext all actually
    work together, not just in isolation. Two separate rounds (the model
    sees query's result before deciding to call chart), the realistic case
    the turn-scoping fix targets.
    """
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
                            "title": "Members per server",
                            "x_field": "server_id",
                            "y_field": "approximate_member_count",
                        },
                    )
                ],
            ),
            AssistantTurn(text="Here's a bar chart of members per server.", tool_calls=[]),
        ]
    )

    resp = await client.post("/chat", json={"message": "chart member counts per server"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "Here's a bar chart of members per server."
    assert len(body["tool_calls"]) == 2
    query_call, chart_call = body["tool_calls"]
    assert query_call["name"] == "query"
    assert chart_call["name"] == "chart"
    assert chart_call["is_error"] is False
    assert chart_call["result"]["chart_type"] == "bar"
    assert {row["server_id"] for row in chart_call["result"]["data"]} == {"srv_1", "srv_2"}
    assert "approximate_member_count" in chart_call["result"]["sql"]
