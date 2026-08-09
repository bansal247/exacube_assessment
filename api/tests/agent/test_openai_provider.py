"""OpenAIProvider's message/response translation, against a mocked
AsyncOpenAI client -- no network, no real key needed. Mirrors the same
concern AnthropicProvider has: translating to/from a provider's specific
wire format.
"""

from types import SimpleNamespace

import pytest

from app.agent.messages import Message, ToolCall
from app.agent.openai_provider import OpenAIProvider
from app.agent.provider import ToolSchema


@pytest.fixture
def provider():
    return OpenAIProvider(api_key="fake-key", model="gpt-4o-mini")


@pytest.mark.asyncio
async def test_generate_text_only_response(provider, monkeypatch):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hello there", tool_calls=None))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )

    async def fake_create(**kwargs):
        return response

    monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)

    turn = await provider.generate("system", [Message(role="user", content="hi")], [])

    assert turn.text == "hello there"
    assert turn.tool_calls == []
    assert turn.input_tokens == 10
    assert turn.output_tokens == 5


@pytest.mark.asyncio
async def test_generate_tool_call_response(provider, monkeypatch):
    raw_tool_call = SimpleNamespace(
        id="call_1", function=SimpleNamespace(name="query", arguments='{"sql": "SELECT 1"}')
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[raw_tool_call]))],
        usage=SimpleNamespace(prompt_tokens=20, completion_tokens=8),
    )

    async def fake_create(**kwargs):
        return response

    monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)

    turn = await provider.generate("system", [Message(role="user", content="run a query")], [])

    assert turn.tool_calls == [ToolCall(id="call_1", name="query", arguments={"sql": "SELECT 1"})]


@pytest.mark.asyncio
async def test_generate_passes_tools_as_function_schema(provider, monkeypatch):
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )

    monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)

    tools = [ToolSchema(name="query", description="runs sql", input_schema={"type": "object"})]
    await provider.generate("system", [Message(role="user", content="hi")], tools)

    assert captured["tools"] == [
        {"type": "function", "function": {"name": "query", "description": "runs sql", "parameters": {"type": "object"}}}
    ]


def test_to_openai_messages_translation():
    messages = [
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content=None,
            tool_calls=[ToolCall(id="c1", name="query", arguments={"sql": "SELECT 1"})],
        ),
        Message(role="tool", content="result text", tool_call_id="c1", tool_name="query"),
    ]

    result = OpenAIProvider._to_openai_messages(messages)

    assert result[0] == {"role": "user", "content": "hi"}
    assert result[1]["role"] == "assistant"
    assert result[1]["tool_calls"][0]["id"] == "c1"
    assert result[1]["tool_calls"][0]["function"]["name"] == "query"
    # OpenAI's native "tool" role -- no user-message wrapping needed,
    # unlike Anthropic's tool_result-as-user-message translation.
    assert result[2] == {"role": "tool", "tool_call_id": "c1", "content": "result text"}
