"""Builds and replays the tool-call chain behind a pinned artifact.

A pinned chart isn't just "one SQL string" -- it's the result of a *chain*
of plugin calls (query -> chart, or in the future maybe query -> images, or
just query alone). Storing and re-running that whole chain, rather than
hardcoding "the thing to re-run is SQL," is what makes pinning work for any
plugin or combination of plugins without special-casing -- a future
`images` plugin with no SQL concept at all still fits this shape.
"""

from dataclasses import dataclass
from typing import Any

from app.agent.messages import Message
from app.agent.plugins.base import SOURCE_CALL_ID_ARG, PluginContext, PluginError
from app.agent.plugins.registry import get_plugin
from app.errors import BadRequestError


@dataclass
class ChainStep:
    tool_call_id: str
    plugin_name: str
    arguments: dict


def build_chain(history: list[Message], target_tool_call_id: str) -> list[ChainStep]:
    """Walks backward from target_tool_call_id via SOURCE_CALL_ID_ARG
    (the same convention the loop's `consumes` validation relies on) to
    find every upstream call the target depends on. Returns the chain in
    execution order (earliest call first, target last).
    """
    calls_by_id: dict[str, tuple[str, dict]] = {
        tc.id: (tc.name, tc.arguments) for m in history if m.role == "assistant" for tc in m.tool_calls
    }

    chain: list[ChainStep] = []
    current_id: str | None = target_tool_call_id
    seen: set[str] = set()

    while current_id is not None:
        if current_id in seen:
            raise BadRequestError(f"Circular tool-call chain detected at '{current_id}'")
        seen.add(current_id)

        found = calls_by_id.get(current_id)
        if found is None:
            raise BadRequestError(f"No tool call '{current_id}' found in this session's history")
        plugin_name, arguments = found

        chain.append(ChainStep(tool_call_id=current_id, plugin_name=plugin_name, arguments=arguments))

        plugin = get_plugin(plugin_name)
        if plugin is not None and plugin.consumes is not None:
            current_id = arguments.get(SOURCE_CALL_ID_ARG)
        else:
            current_id = None

    chain.reverse()
    return chain


async def replay_chain(chain: list[ChainStep], context: PluginContext) -> Any:
    """Re-executes each step in order against a fresh context, threading
    prior_results/prior_call_names through exactly as the live loop does,
    and returns the final step's result data. Raises PluginError if any
    step fails -- callers decide how to surface that (the loop turns it
    into a tool-result message; pin refresh turns it into an UpstreamError).
    """
    result_data = None
    for step in chain:
        plugin = get_plugin(step.plugin_name)
        if plugin is None:
            raise PluginError(f"Plugin '{step.plugin_name}' is no longer registered", retryable=False)

        result = await plugin.execute(step.arguments, context)
        context.prior_results[step.tool_call_id] = result.data
        context.prior_call_names[step.tool_call_id] = plugin.name
        result_data = result.data

    return result_data
