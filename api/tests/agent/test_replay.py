"""Unit tests for build_chain() -- the backward walk from a target
tool_call_id through whatever arguments Plugin.consumes declares to
reconstruct a valid execution order for the calls that produced it. No DB,
no provider; just Message history in, ChainStep list out.
"""

import pytest

from app.agent import replay as replay_module
from app.agent.messages import Message, ToolCall
from app.agent.replay import build_chain
from app.errors import BadRequestError


def _assistant(tool_calls):
    return Message(role="assistant", tool_calls=tool_calls)


class _FakePlugin:
    def __init__(self, name, consumes=None):
        self.name = name
        self.consumes = consumes


def _patch_plugins(monkeypatch, plugins: dict[str, "_FakePlugin"]):
    monkeypatch.setattr(replay_module, "get_plugin", lambda name: plugins.get(name))


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


def test_fan_in_chain_includes_both_upstream_dependencies(monkeypatch):
    # combiner consumes two upstream plugins at once -- both must appear in
    # the chain, before combiner itself.
    _patch_plugins(
        monkeypatch,
        {
            "echo": _FakePlugin("echo"),
            "shout": _FakePlugin("shout"),
            "combiner": _FakePlugin("combiner", consumes={"echo_call_id": "echo", "shout_call_id": "shout"}),
        },
    )
    history = [
        _assistant([ToolCall(id="c1", name="echo", arguments={"value": "hi"})]),
        _assistant([ToolCall(id="c2", name="shout", arguments={"value": "HI"})]),
        _assistant([ToolCall(id="c3", name="combiner", arguments={"echo_call_id": "c1", "shout_call_id": "c2"})]),
    ]

    chain = build_chain(history, "c3")

    assert [s.tool_call_id for s in chain] == ["c1", "c2", "c3"]
    assert [s.plugin_name for s in chain] == ["echo", "shout", "combiner"]


def test_diamond_dependency_appears_exactly_once(monkeypatch):
    # c2 and c3 both depend on c1 (query); c4 (combiner) depends on both c2
    # and c3. c1 is reachable via two separate paths but must still appear
    # exactly once in the chain, before both of its dependents.
    _patch_plugins(
        monkeypatch,
        {
            "query": _FakePlugin("query"),
            "chart_a": _FakePlugin("chart_a", consumes={"source_call_id": "query"}),
            "chart_b": _FakePlugin("chart_b", consumes={"source_call_id": "query"}),
            "combiner": _FakePlugin("combiner", consumes={"a_call_id": "chart_a", "b_call_id": "chart_b"}),
        },
    )
    history = [
        _assistant([ToolCall(id="c1", name="query", arguments={"sql": "SELECT 1"})]),
        _assistant([ToolCall(id="c2", name="chart_a", arguments={"source_call_id": "c1"})]),
        _assistant([ToolCall(id="c3", name="chart_b", arguments={"source_call_id": "c1"})]),
        _assistant([ToolCall(id="c4", name="combiner", arguments={"a_call_id": "c2", "b_call_id": "c3"})]),
    ]

    chain = build_chain(history, "c4")

    assert [s.tool_call_id for s in chain] == ["c1", "c2", "c3", "c4"]
    assert len(chain) == 4  # c1 (query) appears once, not twice
