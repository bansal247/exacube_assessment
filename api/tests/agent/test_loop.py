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
from app.agent.plugins.base import SOURCE_CALL_ID_ARG, Plugin, PluginError, PluginResult

from tests.agent.fakes import ScriptedProvider


class EchoPlugin(Plugin):
    name = "echo"
    description = "echoes its input back"
    input_schema = {"type": "object", "properties": {"value": {"type": "string"}}}

    async def execute(self, arguments, context):
        return PluginResult(data={"echoed": arguments.get("value")}, llm_summary=f"echoed {arguments.get('value')!r}")


class AlwaysFailsPlugin(Plugin):
    name = "always_fails"
    description = "always raises"
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, arguments, context):
        raise PluginError("simulated failure", retryable=True)


class ChainReaderPlugin(Plugin):
    """Reads a prior 'echo' call's result out of context -- the mechanism
    behind "chart X, then put it in a deck," and exercises the formal
    `consumes` declaration + loop-level validation.
    """

    name = "chain_reader"
    description = "reads a prior echo result"
    consumes = {SOURCE_CALL_ID_ARG: "echo"}
    input_schema = {
        "type": "object",
        "properties": {"source_call_id": {"type": "string", "description": "tool_call_id of the prior echo call."}},
    }

    async def execute(self, arguments, context):
        prior = context.prior_results.get(arguments["source_call_id"])
        return PluginResult(data={"saw_prior": prior}, llm_summary="chained successfully")


class ShoutPlugin(Plugin):
    name = "shout"
    description = "shouts its input back"
    input_schema = {"type": "object", "properties": {"value": {"type": "string"}}}

    async def execute(self, arguments, context):
        return PluginResult(data={"shouted": arguments.get("value")}, llm_summary=f"shouted {arguments.get('value')!r}")


class CombinerPlugin(Plugin):
    """Fan-in: consumes both a prior 'echo' call and a prior 'shout' call
    at once -- exercises Plugin.consumes as a genuine multi-entry
    {argument_name: plugin_name} map, not just the single-parent case
    every other fixture in this file uses.
    """

    name = "combiner"
    description = "combines a prior echo and a prior shout result"
    consumes = {"echo_call_id": "echo", "shout_call_id": "shout"}
    input_schema = {
        "type": "object",
        "properties": {
            "echo_call_id": {"type": "string", "description": "tool_call_id of the prior echo call."},
            "shout_call_id": {"type": "string", "description": "tool_call_id of the prior shout call."},
        },
    }

    async def execute(self, arguments, context):
        echo = context.prior_results.get(arguments["echo_call_id"])
        shout = context.prior_results.get(arguments["shout_call_id"])
        return PluginResult(data={"echo": echo, "shout": shout}, llm_summary="combined successfully")


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
async def test_consuming_plugin_schema_shows_real_candidate_id_after_upstream_call(monkeypatch):
    """The proactive counterpart to test_consumes_error_lists_real_candidate_ids:
    once echo has actually run, the *next* round's tool schema for
    chain_reader should already contain the real call id in its
    source_call_id description -- not just the error message after a
    failed guess. Found necessary from a live run where the model
    fabricated the same wrong id twice even after being told the real one
    in the error text.
    """
    _patch_registry(monkeypatch, [EchoPlugin(), ChainReaderPlugin()])
    provider = ScriptedProvider(
        [
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c1", name="echo", arguments={"value": "hi"})]),
            AssistantTurn(text="done", tool_calls=[]),
        ]
    )
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    await agent.run(history=[], user_message="echo hi", agent_conn=None)

    # Round 1 (index 1) is the call made *after* echo (id "c1") succeeded.
    _messages_sent, tools_offered = provider.calls[1]
    chain_reader_schema = next(t for t in tools_offered if t.name == "chain_reader")
    source_call_id_description = chain_reader_schema.input_schema["properties"]["source_call_id"]["description"]
    assert "c1" in source_call_id_description
    assert "REAL IDS AVAILABLE" in source_call_id_description


@pytest.mark.asyncio
async def test_candidate_hint_lists_most_recent_upstream_call_first(monkeypatch):
    """Regression test for a real production bug: with two 'echo' calls in
    context (an earlier one from a prior /chat turn, a fresher one from
    this turn), the model referenced the *stale* one -- chart's actual bug
    was a `query` call from several messages earlier in the same session,
    not the query it had just run. The candidate list was chronological
    (oldest first), which is the wrong order for a model that anchors on
    the first item; it must list the most recent call first instead.
    """
    _patch_registry(monkeypatch, [EchoPlugin(), ChainReaderPlugin()])

    first_provider = ScriptedProvider(
        [
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c1", name="echo", arguments={"value": "old"})]),
            AssistantTurn(text="echoed", tool_calls=[]),
        ]
    )
    first_agent = AgentLoop(provider=first_provider, max_tool_retries=2)
    turn_one_messages = await first_agent.run(history=[], user_message="echo old", agent_conn=None)

    second_provider = ScriptedProvider(
        [
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c2", name="echo", arguments={"value": "new"})]),
            AssistantTurn(text="ok", tool_calls=[]),
        ]
    )
    second_agent = AgentLoop(provider=second_provider, max_tool_retries=2)
    await second_agent.run(history=turn_one_messages, user_message="echo new", agent_conn=None)

    # Round 1 (index 1) is the call made *after* the second echo (id "c2")
    # succeeded -- by now both c1 (an earlier turn) and c2 (this turn) are
    # valid candidates.
    _messages_sent, tools_offered = second_provider.calls[1]
    chain_reader_schema = next(t for t in tools_offered if t.name == "chain_reader")
    description = chain_reader_schema.input_schema["properties"]["source_call_id"]["description"]

    assert description.index("c2") < description.index("c1")


@pytest.mark.asyncio
async def test_consuming_plugin_schema_before_any_upstream_call_says_call_it_first(monkeypatch):
    _patch_registry(monkeypatch, [EchoPlugin(), ChainReaderPlugin()])
    provider = ScriptedProvider([AssistantTurn(text="ok", tool_calls=[])])
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    await agent.run(history=[], user_message="hi", agent_conn=None)

    _messages_sent, tools_offered = provider.calls[0]
    chain_reader_schema = next(t for t in tools_offered if t.name == "chain_reader")
    description = chain_reader_schema.input_schema["properties"]["source_call_id"]["description"]
    assert "call 'echo' first" in description


@pytest.mark.asyncio
async def test_tool_schemas_do_not_mutate_shared_plugin_input_schema(monkeypatch):
    """The dynamic hint must never leak back into the plugin's own
    class-level input_schema dict -- that object is shared across every
    request this AgentLoop instance ever handles (a real bug if mutated:
    one user's real call id would silently appear in another's prompt).
    """
    plugin = ChainReaderPlugin()
    original_description = plugin.input_schema["properties"]["source_call_id"]["description"]
    _patch_registry(monkeypatch, [EchoPlugin(), plugin])
    provider = ScriptedProvider(
        [
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c1", name="echo", arguments={"value": "hi"})]),
            AssistantTurn(text="done", tool_calls=[]),
        ]
    )
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    await agent.run(history=[], user_message="echo hi", agent_conn=None)

    assert plugin.input_schema["properties"]["source_call_id"]["description"] == original_description


@pytest.mark.asyncio
async def test_tool_chaining_across_separate_chat_turns(monkeypatch):
    """Regression test for a real production bug: PluginContext was seeded
    fresh (empty prior_results) on every AgentLoop.run() call, so a `chart`
    request in a *later* /chat turn referencing a `query` call from an
    *earlier* turn always failed validation -- "chart it" as a follow-up
    message to an earlier query, the single most natural way a real user
    actually chains tools, was silently broken regardless of what
    tool_call_id the model used. This simulates exactly that: two separate
    agent.run() calls, the second one's `history` argument populated from
    the first call's own returned messages -- the same way ChatService
    reloads persisted history for a new turn.
    """
    _patch_registry(monkeypatch, [EchoPlugin(), ChainReaderPlugin()])

    first_provider = ScriptedProvider(
        [
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c1", name="echo", arguments={"value": "turn-one"})]),
            AssistantTurn(text="echoed", tool_calls=[]),
        ]
    )
    first_agent = AgentLoop(provider=first_provider, max_tool_retries=2)
    turn_one_messages = await first_agent.run(history=[], user_message="echo turn-one", agent_conn=None)

    second_provider = ScriptedProvider(
        [
            AssistantTurn(
                text=None, tool_calls=[ToolCall(id="c2", name="chain_reader", arguments={"source_call_id": "c1"})]
            ),
            AssistantTurn(text="done", tool_calls=[]),
        ]
    )
    second_agent = AgentLoop(provider=second_provider, max_tool_retries=2)
    turn_two_messages = await second_agent.run(
        history=turn_one_messages, user_message="now chain it", agent_conn=None
    )

    tool_msgs = [m for m in turn_two_messages if m.role == "tool"]
    assert tool_msgs[0].is_error is False
    assert tool_msgs[0].data == {"saw_prior": {"echoed": "turn-one"}}


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
async def test_consumes_error_lists_real_candidate_ids(monkeypatch):
    """Regression test for a real production issue: the model repeated the
    identical fabricated source_call_id ("query_1") on both retries rather
    than trying something new, once seen in production logs. The error
    message now lists the actual valid ids so a retry has something
    concrete to copy instead of guessing again.
    """
    _patch_registry(monkeypatch, [EchoPlugin(), ChainReaderPlugin()])
    provider = ScriptedProvider(
        [
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c1", name="echo", arguments={"value": "hi"})]),
            AssistantTurn(
                text=None, tool_calls=[ToolCall(id="c2", name="chain_reader", arguments={"source_call_id": "made-up-id"})]
            ),
            AssistantTurn(text="gave up", tool_calls=[]),
        ]
    )
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    new_messages = await agent.run(history=[], user_message="echo then chain with a bad id", agent_conn=None)

    tool_msgs = [m for m in new_messages if m.role == "tool"]
    assert tool_msgs[1].is_error is True
    assert "c1" in tool_msgs[1].content
    assert "Valid 'echo' call ids" in tool_msgs[1].content


@pytest.mark.asyncio
async def test_consumes_wrong_source_plugin_type_is_a_structured_error(monkeypatch):
    class OtherPlugin(Plugin):
        name = "other"
        description = "a plugin that isn't 'echo'"
        input_schema = {"type": "object", "properties": {}}

        async def execute(self, arguments, context):
            return PluginResult(data={"ok": True}, llm_summary="did something")

    _patch_registry(monkeypatch, [OtherPlugin(), ChainReaderPlugin()])
    provider = ScriptedProvider(
        [
            # chain_reader declares consumes={"source_call_id": "echo"}, but
            # c1 here is 'other' -- succeeds, just isn't the right kind of call.
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

        async def execute(self, arguments, context):
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


@pytest.mark.asyncio
async def test_fan_in_consumes_succeeds_with_both_upstream_calls(monkeypatch):
    """A plugin can consume more than one upstream plugin at once --
    combiner needs both a prior echo and a prior shout call, referenced by
    two separate arguments, not the single source_call_id every other
    fixture here uses.
    """
    _patch_registry(monkeypatch, [EchoPlugin(), ShoutPlugin(), CombinerPlugin()])
    provider = ScriptedProvider(
        [
            AssistantTurn(
                text=None,
                tool_calls=[
                    ToolCall(id="c1", name="echo", arguments={"value": "hi"}),
                    ToolCall(id="c2", name="shout", arguments={"value": "HI"}),
                ],
            ),
            AssistantTurn(
                text=None,
                tool_calls=[ToolCall(id="c3", name="combiner", arguments={"echo_call_id": "c1", "shout_call_id": "c2"})],
            ),
            AssistantTurn(text="done", tool_calls=[]),
        ]
    )
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    new_messages = await agent.run(history=[], user_message="echo and shout, then combine", agent_conn=None)

    tool_msgs = [m for m in new_messages if m.role == "tool"]
    combiner_msg = next(m for m in tool_msgs if m.tool_name == "combiner")
    assert combiner_msg.is_error is False
    assert combiner_msg.data == {"echo": {"echoed": "hi"}, "shout": {"shouted": "HI"}}


@pytest.mark.asyncio
async def test_fan_in_consumes_reports_every_missing_argument_at_once(monkeypatch):
    """Both bad arguments should be named in a single error -- a fan-in
    plugin missing two upstream references shouldn't need two separate
    round-trips to find out about the second one.
    """
    _patch_registry(monkeypatch, [EchoPlugin(), ShoutPlugin(), CombinerPlugin()])
    provider = ScriptedProvider(
        [
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c1", name="combiner", arguments={})]),
            AssistantTurn(text="gave up", tool_calls=[]),
        ]
    )
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    new_messages = await agent.run(history=[], user_message="combine without either source", agent_conn=None)

    tool_msgs = [m for m in new_messages if m.role == "tool"]
    assert tool_msgs[0].is_error is True
    assert "echo_call_id" in tool_msgs[0].content
    assert "shout_call_id" in tool_msgs[0].content
