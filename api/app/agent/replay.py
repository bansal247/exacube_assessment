"""Builds and replays the tool-call chain behind a pinned artifact.

A pinned chart isn't just "one SQL string" -- it's the result of a *chain*
of plugin calls (query -> chart, or a fan-in like {chart, image} -> pdf, or
just query alone). Storing and re-running that whole chain, rather than
hardcoding "the thing to re-run is SQL," is what makes pinning work for any
plugin or combination of plugins without special-casing -- a future plugin
that consumes more than one upstream plugin still fits this shape.
"""

from dataclasses import dataclass
from typing import Any

from app.agent.messages import Message
from app.agent.plugins.base import PluginContext, PluginError
from app.agent.plugins.registry import get_plugin
from app.errors import BadRequestError


@dataclass
class ChainStep:
    tool_call_id: str
    plugin_name: str
    arguments: dict


def build_chain(history: list[Message], target_tool_call_id: str) -> list[ChainStep]:
    """Walks backward from target_tool_call_id through every argument
    Plugin.consumes declares (the same map the loop's own validation
    reads) to find every upstream call the target depends on -- a DAG in
    general, not just a line, once a plugin consumes more than one
    upstream. Returns the chain in a valid execution order: every step
    appears after all of its own dependencies, and a call reachable via
    more than one path (a diamond -- two steps sharing the same upstream
    query, say) still appears exactly once.
    """
    calls_by_id: dict[str, tuple[str, dict]] = {
        tc.id: (tc.name, tc.arguments) for m in history if m.role == "assistant" for tc in m.tool_calls
    }

    chain: list[ChainStep] = []
    added: set[str] = set()
    in_progress: set[str] = set()

    def visit(call_id: str) -> None:
        if call_id in added:
            return
        if call_id in in_progress:
            raise BadRequestError(f"Circular tool-call chain detected at '{call_id}'")
        in_progress.add(call_id)

        found = calls_by_id.get(call_id)
        if found is None:
            raise BadRequestError(f"No tool call '{call_id}' found in this session's history")
        plugin_name, arguments = found

        plugin = get_plugin(plugin_name)
        if plugin is not None and plugin.consumes:
            for arg_name in plugin.consumes:
                upstream_id = arguments.get(arg_name)
                if upstream_id:
                    visit(upstream_id)

        in_progress.discard(call_id)
        added.add(call_id)
        chain.append(ChainStep(tool_call_id=call_id, plugin_name=plugin_name, arguments=arguments))

    visit(target_tool_call_id)
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
