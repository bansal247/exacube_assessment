"""Unit tests for the core agent loop, against a scripted fake provider and
fake plugins -- no network, no LLM call, no DB. This is deliberately the
same "test the logic without HTTP (or in this case, without a live LLM)"
philosophy as Part 2: the loop's decide/retry/decline/chain behavior is
provider- and plugin-agnostic, so it should be provable without either.
"""

import pytest

from app.agent import loop as loop_module
from app.agent.loop import AgentLoop
from app.agent.messages import AssistantTurn, Message, ToolCall
from app.agent.plugins.base import Plugin, PluginError, PluginResult

from tests.agent.fakes import ScriptedProvider


class EchoPlugin(Plugin):
    name = "echo"
    description = "echoes its input back"
    input_schema = {"type": "object", "properties": {"value": {"type": "string"}}}

    async def execute(self, arguments, context, on_progress=None):
        return PluginResult(data={"echoed": arguments.get("value")}, llm_summary=f"echoed {arguments.get('value')!r}")


class AlwaysFailsPlugin(Plugin):
    name = "always_fails"
    description = "always raises"
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, arguments, context, on_progress=None):
        raise PluginError("simulated failure", retryable=True)


class ChainReaderPlugin(Plugin):
    """Reads a prior 'echo' call's result out of context -- the mechanism
    behind "chart X, then put it in a deck," and exercises the formal
    `consumes` declaration + loop-level validation.
    """

    name = "chain_reader"
    description = "reads a prior echo result"
    consumes = "echo"
    input_schema = {"type": "object", "properties": {"source_call_id": {"type": "string"}}}

    async def execute(self, arguments, context, on_progress=None):
        prior = context.prior_results.get(arguments["source_call_id"])
        return PluginResult(data={"saw_prior": prior}, llm_summary="chained successfully")


def _patch_registry(monkeypatch, plugins: list[Plugin]):
    by_name = {p.name: p for p in plugins}
    monkeypatch.setattr(loop_module, "all_plugins", lambda: list(plugins))
    monkeypatch.setattr(loop_module, "get_plugin", lambda name: by_name.get(name))


@pytest.mark.asyncio
async def test_query_happy_path_calls_tool_then_answers(monkeypatch):
    _patch_registry(monkeypatch, [EchoPlugin()])
    provider = ScriptedProvider(
        [
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c1", name="echo", arguments={"value": "hi"})]),
            AssistantTurn(text="The tool echoed: hi", tool_calls=[]),
        ]
    )
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    new_messages = await agent.run(history=[], user_message="echo hi", agent_conn=None)

    assert new_messages[0] == Message(role="user", content="echo hi")
    assert new_messages[-1].role == "assistant"
    assert new_messages[-1].content == "The tool echoed: hi"
    tool_msgs = [m for m in new_messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].is_error is False
    # content is the short LLM-facing summary; data is the full payload.
    assert tool_msgs[0].content == "echoed 'hi'"
    assert tool_msgs[0].data == {"echoed": "hi"}


@pytest.mark.asyncio
async def test_decline_with_no_tool_call_returns_immediately():
    provider = ScriptedProvider([AssistantTurn(text="I can't answer that from this dataset.", tool_calls=[])])
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    new_messages = await agent.run(history=[], user_message="what's the weather?", agent_conn=None)

    assert len(provider.calls) == 1
    assert new_messages[-1].content == "I can't answer that from this dataset."
    assert not any(m.role == "tool" for m in new_messages)


@pytest.mark.asyncio
async def test_recovers_after_one_failure(monkeypatch):
    _patch_registry(monkeypatch, [AlwaysFailsPlugin(), EchoPlugin()])
    provider = ScriptedProvider(
        [
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c1", name="always_fails", arguments={})]),
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c2", name="echo", arguments={"value": "ok"})]),
            AssistantTurn(text="Recovered", tool_calls=[]),
        ]
    )
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    new_messages = await agent.run(history=[], user_message="try something flaky", agent_conn=None)

    assert new_messages[-1].content == "Recovered"
    tool_msgs = [m for m in new_messages if m.role == "tool"]
    assert [m.is_error for m in tool_msgs] == [True, False]
    # third generate() call must still have been offered tools -- only one
    # failed round happened, budget is 2, so tools shouldn't be withdrawn yet
    assert len(provider.calls[2][1]) > 0


@pytest.mark.asyncio
async def test_bounded_retries_then_graceful_surrender_no_tools_offered(monkeypatch):
    _patch_registry(monkeypatch, [AlwaysFailsPlugin()])
    provider = ScriptedProvider(
        [
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c1", name="always_fails", arguments={})]),
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c2", name="always_fails", arguments={})]),
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c3", name="always_fails", arguments={})]),
            AssistantTurn(text="I couldn't complete that query after retrying.", tool_calls=[]),
        ]
    )
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    new_messages = await agent.run(history=[], user_message="try something broken", agent_conn=None)

    assert new_messages[-1].content == "I couldn't complete that query after retrying."
    # 3 failed rounds used up the budget of 2 retries (1 initial + 2 retries
    # = 3 tool-call attempts) -- the 4th generate() call must have been
    # offered zero tools, forcing a prose-only answer.
    assert provider.calls[3][1] == []


@pytest.mark.asyncio
async def test_tool_chaining_second_call_sees_first_result_same_round(monkeypatch):
    _patch_registry(monkeypatch, [EchoPlugin(), ChainReaderPlugin()])
    provider = ScriptedProvider(
        [
            AssistantTurn(
                text=None,
                tool_calls=[
                    ToolCall(id="c1", name="echo", arguments={"value": "chart-data"}),
                    ToolCall(id="c2", name="chain_reader", arguments={"source_call_id": "c1"}),
                ],
            ),
            AssistantTurn(text="Chained successfully", tool_calls=[]),
        ]
    )
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    new_messages = await agent.run(history=[], user_message="chart it then chain it", agent_conn=None)

    tool_msgs = [m for m in new_messages if m.role == "tool"]
    assert tool_msgs[0].tool_name == "echo"
    assert tool_msgs[1].tool_name == "chain_reader"
    assert tool_msgs[1].data == {"saw_prior": {"echoed": "chart-data"}}


@pytest.mark.asyncio
async def test_tool_chaining_across_separate_rounds(monkeypatch):
    """The realistic case: the model calls echo, sees the result in a
    follow-up provider response, *then* decides to call chain_reader --
    two separate rounds, not one batched assistant message. Regression test
    for the bug where PluginContext was recreated per round instead of once
    per turn.
    """
    _patch_registry(monkeypatch, [EchoPlugin(), ChainReaderPlugin()])
    provider = ScriptedProvider(
        [
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c1", name="echo", arguments={"value": "round-one"})]),
            AssistantTurn(
                text=None, tool_calls=[ToolCall(id="c2", name="chain_reader", arguments={"source_call_id": "c1"})]
            ),
            AssistantTurn(text="done", tool_calls=[]),
        ]
    )
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    new_messages = await agent.run(history=[], user_message="echo then chain across turns", agent_conn=None)

    tool_msgs = [m for m in new_messages if m.role == "tool"]
    assert tool_msgs[1].is_error is False
    assert tool_msgs[1].data == {"saw_prior": {"echoed": "round-one"}}


@pytest.mark.asyncio
async def test_consumes_missing_source_call_id_is_a_structured_error(monkeypatch):
    _patch_registry(monkeypatch, [EchoPlugin(), ChainReaderPlugin()])
    provider = ScriptedProvider(
        [
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c1", name="chain_reader", arguments={})]),
            AssistantTurn(text="couldn't chain", tool_calls=[]),
        ]
    )
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    new_messages = await agent.run(history=[], user_message="chain without a source", agent_conn=None)

    tool_msgs = [m for m in new_messages if m.role == "tool"]
    assert tool_msgs[0].is_error is True
    assert "source_call_id" in tool_msgs[0].content


@pytest.mark.asyncio
async def test_consumes_missing_source_call_id_reference_is_a_structured_error(monkeypatch):
    _patch_registry(monkeypatch, [EchoPlugin(), ChainReaderPlugin()])
    provider = ScriptedProvider(
        [
            AssistantTurn(
                text=None, tool_calls=[ToolCall(id="c1", name="chain_reader", arguments={"source_call_id": "does-not-exist"})]
            ),
            AssistantTurn(text="gave up", tool_calls=[]),
        ]
    )
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    new_messages = await agent.run(history=[], user_message="chain with a nonexistent reference", agent_conn=None)

    tool_msgs = [m for m in new_messages if m.role == "tool"]
    assert tool_msgs[0].is_error is True
    assert "does not refer to a completed call" in tool_msgs[0].content


@pytest.mark.asyncio
async def test_consumes_wrong_source_plugin_type_is_a_structured_error(monkeypatch):
    class OtherPlugin(Plugin):
        name = "other"
        description = "a plugin that isn't 'echo'"
        input_schema = {"type": "object", "properties": {}}

        async def execute(self, arguments, context, on_progress=None):
            return PluginResult(data={"ok": True}, llm_summary="did something")

    _patch_registry(monkeypatch, [OtherPlugin(), ChainReaderPlugin()])
    provider = ScriptedProvider(
        [
            # chain_reader declares consumes="echo", but c1 here is 'other'
            # -- succeeds, just isn't the right kind of call.
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c1", name="other", arguments={})]),
            AssistantTurn(
                text=None, tool_calls=[ToolCall(id="c2", name="chain_reader", arguments={"source_call_id": "c1"})]
            ),
            AssistantTurn(text="gave up", tool_calls=[]),
        ]
    )
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    new_messages = await agent.run(history=[], user_message="chain with the wrong kind of call", agent_conn=None)

    tool_msgs = [m for m in new_messages if m.role == "tool"]
    assert tool_msgs[1].is_error is True
    assert "requires a 'echo' call" in tool_msgs[1].content


@pytest.mark.asyncio
async def test_unknown_tool_name_is_reported_as_error_not_a_crash(monkeypatch):
    _patch_registry(monkeypatch, [])
    provider = ScriptedProvider(
        [
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c1", name="does_not_exist", arguments={})]),
            AssistantTurn(text="That tool isn't available.", tool_calls=[]),
        ]
    )
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    new_messages = await agent.run(history=[], user_message="use a fake tool", agent_conn=None)

    tool_msgs = [m for m in new_messages if m.role == "tool"]
    assert tool_msgs[0].is_error is True
    assert "Unknown tool" in tool_msgs[0].content


@pytest.mark.asyncio
async def test_plugin_bug_unhandled_exception_does_not_crash_loop(monkeypatch):
    class BuggyPlugin(Plugin):
        name = "buggy"
        description = "raises a non-PluginError bug"
        input_schema = {"type": "object", "properties": {}}

        async def execute(self, arguments, context, on_progress=None):
            raise RuntimeError("boom")

    _patch_registry(monkeypatch, [BuggyPlugin()])
    provider = ScriptedProvider(
        [
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c1", name="buggy", arguments={})]),
            AssistantTurn(text="Something went wrong internally.", tool_calls=[]),
        ]
    )
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    new_messages = await agent.run(history=[], user_message="trigger the bug", agent_conn=None)

    tool_msgs = [m for m in new_messages if m.role == "tool"]
    assert tool_msgs[0].is_error is True
    assert new_messages[-1].content == "Something went wrong internally."


@pytest.mark.asyncio
async def test_history_is_passed_through_and_new_user_message_appended(monkeypatch):
    _patch_registry(monkeypatch, [])
    provider = ScriptedProvider([AssistantTurn(text="second reply", tool_calls=[])])
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    history = [Message(role="user", content="first"), Message(role="assistant", content="first reply")]
    new_messages = await agent.run(history=history, user_message="second", agent_conn=None)

    sent_messages, _ = provider.calls[0]
    assert sent_messages[0].content == "first"
    assert sent_messages[-1].content == "second"
    assert new_messages[0] == Message(role="user", content="second")
