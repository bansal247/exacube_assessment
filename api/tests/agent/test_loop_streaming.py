"""Tests for AgentLoop.run_streaming(): the stage events themselves, and
the client-disconnect/cancellation behavior the brief calls out explicitly
("handle the client disconnecting mid-stream without leaking a ...
query"). No network, no live LLM -- ScriptedProvider.generate_stream()
stands in for Anthropic's streaming API.
"""

import asyncio

import pytest

from app.agent import loop as loop_module
from app.agent.loop import AgentLoop
from app.agent.messages import AssistantTurn, ToolCall
from app.agent.plugins.base import Plugin, PluginError, PluginResult
from app.agent.stream_events import FinalAnswer, Reasoning, ToolProgress, ToolResult, ToolSelected

from tests.agent.fakes import ScriptedProvider

SESSION_ID = "11111111-1111-1111-1111-111111111111"


class ProgressPlugin(Plugin):
    name = "progress_tool"
    description = "reports two progress updates before finishing"
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, arguments, context, on_progress=None):
        if on_progress:
            await on_progress("step 1")
            await on_progress("step 2")
        return PluginResult(data={"done": True}, llm_summary="finished")


class AlwaysFailsPlugin(Plugin):
    name = "always_fails"
    description = "always raises"
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, arguments, context, on_progress=None):
        raise PluginError("simulated failure", retryable=True)


class SlowCancellablePlugin(Plugin):
    """Blocks until cancelled, recording whether cancellation actually
    reached it -- what a real in-flight DB query being cancelled on client
    disconnect would look like from the plugin's point of view.
    """

    name = "slow"
    description = "blocks until cancelled"
    input_schema = {"type": "object", "properties": {}}

    def __init__(self):
        self.started = asyncio.Event()
        self.was_cancelled = False

    async def execute(self, arguments, context, on_progress=None):
        self.started.set()
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            self.was_cancelled = True
            raise
        return PluginResult(data={}, llm_summary="unreachable")


def _patch_registry(monkeypatch, plugins: list[Plugin]):
    by_name = {p.name: p for p in plugins}
    monkeypatch.setattr(loop_module, "all_plugins", lambda: list(plugins))
    monkeypatch.setattr(loop_module, "get_plugin", lambda name: by_name.get(name))


async def _collect(agent, **kwargs):
    return [event async for event in agent.run_streaming(**kwargs)]


@pytest.mark.asyncio
async def test_streaming_decline_yields_session_then_final_answer():
    provider = ScriptedProvider([AssistantTurn(text="I can't answer that from this dataset.", tool_calls=[])])
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    events = await _collect(
        agent, history=[], user_message="what's the weather?", agent_conn=None, session_id=SESSION_ID
    )

    # SessionStarted is yielded by ChatService (which owns session
    # creation/lookup), not AgentLoop -- covered in test_chat_stream.py,
    # which drives the full service+router path. Here at the loop level,
    # the first thing to expect is the FinalAnswer, since a decline has no
    # tool calls at all.
    kinds = [type(e).__name__ for e in events]
    assert kinds[0] == "FinalAnswer"
    assert kinds[-1] == "TurnMessages"
    final = next(e for e in events if isinstance(e, FinalAnswer))
    assert final.text == "I can't answer that from this dataset."
    assert not any(isinstance(e, (ToolSelected, ToolResult)) for e in events)


@pytest.mark.asyncio
async def test_streaming_reasoning_text_deltas_are_emitted():
    provider = ScriptedProvider(
        [AssistantTurn(text="Here is my answer.", tool_calls=[])],
        text_deltas_by_index={0: ["Here ", "is ", "my ", "answer."]},
    )
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    events = await _collect(agent, history=[], user_message="hi", agent_conn=None, session_id=SESSION_ID)

    reasoning_chunks = [e.text for e in events if isinstance(e, Reasoning)]
    assert reasoning_chunks == ["Here ", "is ", "my ", "answer."]


@pytest.mark.asyncio
async def test_streaming_tool_call_emits_selected_progress_and_result(monkeypatch):
    _patch_registry(monkeypatch, [ProgressPlugin()])
    provider = ScriptedProvider(
        [
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c1", name="progress_tool", arguments={})]),
            AssistantTurn(text="Done.", tool_calls=[]),
        ]
    )
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    events = await _collect(agent, history=[], user_message="run it", agent_conn=None, session_id=SESSION_ID)

    kinds = [type(e).__name__ for e in events]
    # Stages arrive in the brief's own order: reasoning (none here) -> tool
    # picked -> progress -> result -> prose.
    assert kinds.index("ToolSelected") < kinds.index("ToolProgress")
    assert kinds.index("ToolProgress") < kinds.index("ToolResult")
    assert kinds.index("ToolResult") < kinds.index("FinalAnswer")

    progress_msgs = [e.message for e in events if isinstance(e, ToolProgress)]
    assert progress_msgs == ["step 1", "step 2"]

    result = next(e for e in events if isinstance(e, ToolResult))
    assert result.is_error is False
    assert result.result == {"done": True}


@pytest.mark.asyncio
async def test_streaming_tool_error_is_reported_not_a_crash(monkeypatch):
    _patch_registry(monkeypatch, [AlwaysFailsPlugin()])
    provider = ScriptedProvider(
        [
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c1", name="always_fails", arguments={})]),
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c2", name="always_fails", arguments={})]),
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c3", name="always_fails", arguments={})]),
            AssistantTurn(text="Gave up.", tool_calls=[]),
        ]
    )
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    events = await _collect(agent, history=[], user_message="try", agent_conn=None, session_id=SESSION_ID)

    results = [e for e in events if isinstance(e, ToolResult)]
    assert all(r.is_error for r in results)
    assert isinstance(events[-2], FinalAnswer)
    assert events[-2].text == "Gave up."


@pytest.mark.asyncio
async def test_streaming_persists_via_turn_messages_sentinel(monkeypatch):
    """TurnMessages isn't part of the public LoopStreamEvent union but is
    always the literal last yielded item -- this is what ChatService relies
    on to know when to persist.
    """
    provider = ScriptedProvider([AssistantTurn(text="ok", tool_calls=[])])
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    events = await _collect(agent, history=[], user_message="hi", agent_conn=None, session_id=SESSION_ID)

    assert type(events[-1]).__name__ == "TurnMessages"
    assert len(events[-1].messages) == 2  # user message + assistant reply


@pytest.mark.asyncio
async def test_disconnect_mid_tool_call_cancels_the_plugin(monkeypatch):
    """The brief's own requirement: handle the client disconnecting
    mid-stream without leaking a query.

    Models a disconnect the way Starlette actually produces one: it cancels
    the task that's driving the response, not calling .aclose() on some
    inert generator from an unrelated coroutine. Cancelling `driver` below
    delivers CancelledError wherever the generator is currently suspended
    (here, the queue-drain loop's `await queue.get()`), which is what
    triggers the loop's own `finally: task.cancel()` cleanup of the
    in-flight plugin call.
    """
    slow = SlowCancellablePlugin()
    _patch_registry(monkeypatch, [slow])
    provider = ScriptedProvider([AssistantTurn(text=None, tool_calls=[ToolCall(id="c1", name="slow", arguments={})])])
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    gen = agent.run_streaming(history=[], user_message="run the slow one", agent_conn=None, session_id=SESSION_ID)

    async def drive():
        async for _event in gen:
            pass

    driver = asyncio.create_task(drive())
    await slow.started.wait()
    driver.cancel()
    with pytest.raises(asyncio.CancelledError):
        await driver

    assert slow.was_cancelled is True
