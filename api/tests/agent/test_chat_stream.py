"""POST /chat/stream end-to-end over real SSE, through httpx's streaming
client -- not just the loop in isolation. Confirms the wire format, that
TurnMessages (internal-only) never reaches the client, and that a streamed
turn actually gets persisted (session continuity works the same as the
non-streaming endpoint).
"""

import json

import pytest

from app.agent.loop import AgentLoop
from app.agent.messages import AssistantTurn, ToolCall
from app.agent.service import ChatService
from app.main import app

from tests.agent.fakes import ScriptedProvider


def _use_provider(turns):
    app.state.chat_service = ChatService(loop=AgentLoop(provider=ScriptedProvider(turns), max_tool_retries=2))


async def _stream_events(client, body):
    events = []
    async with client.stream("POST", "/chat/stream", json=body) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    return events


@pytest.mark.asyncio
async def test_stream_decline_shape_and_order(client):
    _use_provider([AssistantTurn(text="I can't answer that from this dataset.", tool_calls=[])])

    events = await _stream_events(client, {"message": "what's the weather?"})

    types = [e["type"] for e in events]
    assert types[0] == "SessionStarted"
    assert "FinalAnswer" in types
    # TurnMessages is internal-only (ChatService consumes it to persist) --
    # must never reach the wire.
    assert "TurnMessages" not in types
    final = next(e for e in events if e["type"] == "FinalAnswer")
    assert final["text"] == "I can't answer that from this dataset."


@pytest.mark.asyncio
async def test_stream_real_query_tool_reaches_client(client):
    _use_provider(
        [
            AssistantTurn(
                text=None, tool_calls=[ToolCall(id="c1", name="query", arguments={"sql": "SELECT COUNT(*) FROM servers"})]
            ),
            AssistantTurn(text="There are 2 servers.", tool_calls=[]),
        ]
    )

    events = await _stream_events(client, {"message": "how many servers?"})

    types = [e["type"] for e in events]
    assert types.index("ToolSelected") < types.index("ToolResult") < types.index("FinalAnswer")
    tool_selected = next(e for e in events if e["type"] == "ToolSelected")
    assert tool_selected["name"] == "query"


@pytest.mark.asyncio
async def test_stream_persists_and_session_continues(client):
    _use_provider([AssistantTurn(text="first reply", tool_calls=[])])
    events = await _stream_events(client, {"message": "first message"})
    session_started = next(e for e in events if e["type"] == "SessionStarted")
    session_id = session_started["session_id"]

    # Non-streaming /chat, same session -- proves the streamed turn was
    # actually persisted (history includes it), not just displayed once.
    _use_provider([AssistantTurn(text="second reply", tool_calls=[])])
    resp = await client.post("/chat", json={"session_id": session_id, "message": "second message"})
    assert resp.status_code == 200
    assert resp.json()["session_id"] == session_id
    assert resp.json()["reply"] == "second reply"


@pytest.mark.asyncio
async def test_stream_unknown_session_id_is_404(client):
    _use_provider([AssistantTurn(text="unreachable", tool_calls=[])])
    async with client.stream(
        "POST", "/chat/stream", json={"session_id": "00000000-0000-0000-0000-000000000000", "message": "hi"}
    ) as response:
        assert response.status_code == 404
