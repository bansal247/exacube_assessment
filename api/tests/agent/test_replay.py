"""Unit tests for build_chain() -- the backward walk from a target
tool_call_id through SOURCE_CALL_ID_ARG references to reconstruct the
ordered chain of calls that produced it. No DB, no provider; just Message
history in, ChainStep list out.
"""

import pytest

from app.agent.messages import Message, ToolCall
from app.agent.replay import build_chain
from app.errors import BadRequestError


def _assistant(tool_calls):
    return Message(role="assistant", tool_calls=tool_calls)


def test_single_call_no_chaining():
    history = [_assistant([ToolCall(id="c1", name="query", arguments={"sql": "SELECT 1"})])]

    chain = build_chain(history, "c1")

    assert [s.tool_call_id for s in chain] == ["c1"]
    assert chain[0].plugin_name == "query"


def test_two_step_chain_in_order():
    history = [
        _assistant([ToolCall(id="c1", name="query", arguments={"sql": "SELECT 1"})]),
        _assistant([ToolCall(id="c2", name="chart", arguments={"source_call_id": "c1", "chart_type": "bar"})]),
    ]

    chain = build_chain(history, "c2")

    assert [s.tool_call_id for s in chain] == ["c1", "c2"]
    assert [s.plugin_name for s in chain] == ["query", "chart"]


def test_unknown_target_raises():
    history = [_assistant([ToolCall(id="c1", name="query", arguments={"sql": "SELECT 1"})])]

    with pytest.raises(BadRequestError):
        build_chain(history, "does-not-exist")


def test_dangling_source_reference_raises():
    # chart references a source_call_id that never happened -- shouldn't be
    # reachable in practice (the loop validates consumes before execute()
    # ever runs), but build_chain must not silently produce a broken chain
    # if it somehow is.
    history = [
        _assistant([ToolCall(id="c2", name="chart", arguments={"source_call_id": "missing", "chart_type": "bar"})])
    ]

    with pytest.raises(BadRequestError):
        build_chain(history, "c2")


def test_self_referential_chain_raises_not_infinite_loops():
    history = [
        _assistant([ToolCall(id="c1", name="chart", arguments={"source_call_id": "c1", "chart_type": "bar"})])
    ]

    with pytest.raises(BadRequestError, match="Circular"):
        build_chain(history, "c1")
