"""Regression test for a real bug: logger.info(..., extra={"name": ...})
raises KeyError at runtime (LogRecord already has a "name" attribute --
the logger's own name -- and stdlib logging forbids overwriting it via
extra), but py_compile can't catch it since it's only a problem when the
line actually executes. Drives the loop for real (not just imports it) so
every logging call site in loop.py actually runs, with a real logging
config attached (not the default no-op handler), catching this class of
bug the way it was actually found -- a live run producing a 500, not a
static check.
"""

import logging

import pytest

from app.agent import loop as loop_module
from app.agent.loop import AgentLoop
from app.agent.messages import AssistantTurn, ToolCall
from app.agent.plugins.base import Plugin, PluginResult
from app.logging_config import JsonFormatter

from tests.agent.fakes import ScriptedProvider


class LoggingEchoPlugin(Plugin):
    name = "echo"
    description = "echoes its input back"
    input_schema = {"type": "object", "properties": {"value": {"type": "string"}}}
    display_kind = "table"

    async def execute(self, arguments, context):
        return PluginResult(data={"echoed": arguments.get("value")}, llm_summary="ok")


@pytest.fixture(autouse=True)
def real_json_logging_handler():
    """Attaches a real handler+formatter AND sets the level to INFO for the
    duration of this test. Both matter: Logger.info() checks
    isEnabledFor(INFO) before calling makeRecord() at all, and the root
    logger's default effective level is WARNING -- without explicitly
    lowering it, these .info() calls would silently no-op and never
    exercise makeRecord()'s extra-key collision check, defeating the point
    of this test. This mirrors what production configure_logging() does
    (attach handler + set level), which is what actually reproduced the bug.
    """
    root = logging.getLogger()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    original_handlers = root.handlers
    original_level = root.level
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    try:
        yield
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)


@pytest.mark.asyncio
async def test_tool_call_logging_does_not_raise(monkeypatch):
    by_name = {"echo": LoggingEchoPlugin()}
    monkeypatch.setattr(loop_module, "all_plugins", lambda: list(by_name.values()))
    monkeypatch.setattr(loop_module, "get_plugin", lambda name: by_name.get(name))

    provider = ScriptedProvider(
        [
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c1", name="echo", arguments={"value": "hi"})]),
            AssistantTurn(text="done", tool_calls=[]),
        ]
    )
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    # Would raise KeyError("Attempt to overwrite 'name' in LogRecord") at
    # the "tool selected" log call before this fix.
    new_messages = await agent.run(history=[], user_message="echo hi", agent_conn=None)

    assert new_messages[-1].content == "done"


@pytest.mark.asyncio
async def test_unknown_tool_logging_does_not_raise(monkeypatch):
    monkeypatch.setattr(loop_module, "all_plugins", lambda: [])
    monkeypatch.setattr(loop_module, "get_plugin", lambda name: None)

    provider = ScriptedProvider(
        [
            AssistantTurn(text=None, tool_calls=[ToolCall(id="c1", name="does_not_exist", arguments={})]),
            AssistantTurn(text="done", tool_calls=[]),
        ]
    )
    agent = AgentLoop(provider=provider, max_tool_retries=2)

    new_messages = await agent.run(history=[], user_message="use a fake tool", agent_conn=None)

    assert new_messages[-1].content == "done"
