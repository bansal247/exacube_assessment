"""The core agent loop: decide -> call tool(s) -> observe -> retry on
failure (bounded) -> respond, or chain into another tool call. Provider- and
plugin-agnostic -- it only knows the Message/ToolCall shapes and the Plugin
contract, never Anthropic or any specific plugin by name.

run() is the original blocking call-and-collect version. run_streaming()
does the same work but yields stage events as they happen (see
stream_events.py) -- both share the tool-call machinery below
(_validate_consumes/_error_message); they don't share control-flow methods
because a generator's cancellation/cleanup semantics on client disconnect
are different enough from a plain coroutine's that forcing one shape to
serve both seemed likelier to hide a bug than to save code.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

import asyncpg

from app.agent.messages import Message, ToolCall
from app.agent.plugins.base import SOURCE_CALL_ID_ARG, Plugin, PluginContext, PluginError
from app.agent.plugins.registry import all_plugins, get_plugin
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.provider import LLMProvider, TextDelta, ToolSchema, TurnComplete
from app.agent.stream_events import FinalAnswer, LoopStreamEvent, Reasoning, StreamError, ToolProgress, ToolResult, ToolSelected

logger = logging.getLogger(__name__)

# Hard ceiling on total provider calls in one turn, independent of the
# configurable failure-retry budget -- guards against a pathological case
# where every tool call *succeeds* but the model just keeps chaining
# indefinitely (cost/latency runaway, not a correctness bound).
MAX_ITERATIONS = 8


@dataclass
class TurnMessages:
    """Internal-only, always the last item run_streaming() yields: the
    complete new-messages list for this turn, for the service layer to
    persist. Not part of LoopStreamEvent -- callers that only care about
    display events can ignore it; the service layer specifically consumes
    and strips it before anything reaches the router/SSE encoder.
    """

    messages: list[Message]


class AgentLoop:
    def __init__(self, provider: LLMProvider, max_tool_retries: int):
        self._provider = provider
        self._max_tool_retries = max_tool_retries

    async def run(self, history: list[Message], user_message: str, agent_conn: asyncpg.Connection) -> list[Message]:
        """Returns the new messages produced this turn (the user message
        plus everything the agent did/said in response) -- callers append
        these to persisted history, they're not mutated in place.

        agent_conn is the request-scoped DB connection (AGENT_DB_USER role)
        threaded through to every plugin call this turn via PluginContext --
        see plugins/base.py for why plugins don't reach for a pool
        themselves.
        """
        messages = [*history, Message(role="user", content=user_message)]
        new_messages: list[Message] = [messages[-1]]

        tools = self._tool_schemas()
        failed_rounds = 0
        # One context for the whole turn, not one per round -- a model that
        # calls query, sees the result, then calls chart in a *follow-up*
        # message (the common case) needs chart's execute() to still see
        # query's result. Scoping this per-round would silently break that.
        context = PluginContext(agent_conn=agent_conn)

        for iteration in range(MAX_ITERATIONS):
            # Once the retry budget is exhausted, stop offering tools at all
            # -- the model is forced to answer in prose (graceful surrender)
            # instead of retrying a broken call indefinitely.
            offer_tools = tools if failed_rounds <= self._max_tool_retries else []

            turn = await self._provider.generate(SYSTEM_PROMPT, messages, offer_tools)

            if not turn.tool_calls:
                assistant_msg = Message(role="assistant", content=turn.text, tool_calls=[])
                messages.append(assistant_msg)
                new_messages.append(assistant_msg)
                return new_messages

            assistant_msg = Message(role="assistant", content=turn.text, tool_calls=turn.tool_calls)
            messages.append(assistant_msg)
            new_messages.append(assistant_msg)

            round_had_error = await self._execute_tool_calls(turn.tool_calls, messages, new_messages, context)
            if round_had_error:
                failed_rounds += 1

        # MAX_ITERATIONS exhausted without a final prose answer -- surrender
        # explicitly rather than silently returning nothing.
        surrender_msg = Message(
            role="assistant",
            content="I wasn't able to complete this within my step budget. Could you rephrase or narrow the question?",
        )
        messages.append(surrender_msg)
        new_messages.append(surrender_msg)
        return new_messages

    async def run_streaming(
        self,
        history: list[Message],
        user_message: str,
        agent_conn: asyncpg.Connection,
        session_id: UUID,
    ) -> AsyncIterator[LoopStreamEvent | TurnMessages]:
        """Same control flow as run(), yielding stage events instead of
        returning at the end. Always ends with exactly one TurnMessages
        (for persistence) after the last user-visible event.

        Client disconnect mid-stream: FastAPI/Starlette closes this
        generator (a GeneratorExit at the current `await`/`yield` point)
        when it detects the client is gone. Any plugin task still running
        at that moment is explicitly cancelled in the `finally` below --
        for the query plugin, cancelling an in-flight `conn.fetch()`
        propagates to Postgres as a real query cancellation, not just an
        abandoned Python coroutine. The DB connection itself is released by
        the router's dependency (get_agent_connection), which runs its own
        cleanup regardless of how this generator exits.
        """
        messages = [*history, Message(role="user", content=user_message)]
        new_messages: list[Message] = [messages[-1]]

        tools = self._tool_schemas()
        failed_rounds = 0
        context = PluginContext(agent_conn=agent_conn)

        for iteration in range(MAX_ITERATIONS):
            offer_tools = tools if failed_rounds <= self._max_tool_retries else []

            turn = None
            async for event in self._provider.generate_stream(SYSTEM_PROMPT, messages, offer_tools):
                if isinstance(event, TextDelta):
                    if event.text:
                        yield Reasoning(text=event.text)
                elif isinstance(event, TurnComplete):
                    turn = event.turn

            if turn is None:
                yield StreamError(message="Provider stream ended without a result.")
                yield TurnMessages(messages=new_messages)
                return

            if not turn.tool_calls:
                assistant_msg = Message(role="assistant", content=turn.text, tool_calls=[])
                messages.append(assistant_msg)
                new_messages.append(assistant_msg)
                yield FinalAnswer(session_id=session_id, text=turn.text or "")
                yield TurnMessages(messages=new_messages)
                return

            assistant_msg = Message(role="assistant", content=turn.text, tool_calls=turn.tool_calls)
            messages.append(assistant_msg)
            new_messages.append(assistant_msg)

            round_had_error = False
            async for event in self._execute_tool_calls_streaming(turn.tool_calls, messages, new_messages, context):
                if isinstance(event, ToolResult) and event.is_error:
                    round_had_error = True
                yield event
            if round_had_error:
                failed_rounds += 1

        surrender_msg = Message(
            role="assistant",
            content="I wasn't able to complete this within my step budget. Could you rephrase or narrow the question?",
        )
        messages.append(surrender_msg)
        new_messages.append(surrender_msg)
        yield FinalAnswer(session_id=session_id, text=surrender_msg.content)
        yield TurnMessages(messages=new_messages)

    async def _execute_tool_calls(
        self,
        tool_calls: list[ToolCall],
        messages: list[Message],
        new_messages: list[Message],
        context: PluginContext,
    ) -> bool:
        round_had_error = False

        for call in tool_calls:
            plugin = get_plugin(call.name)
            tool_msg = await self._run_one_call(call, plugin, context)
            if tool_msg.is_error:
                round_had_error = True
            messages.append(tool_msg)
            new_messages.append(tool_msg)

        return round_had_error

    async def _run_one_call(self, call: ToolCall, plugin: Plugin | None, context: PluginContext) -> Message:
        if plugin is None:
            return self._error_message(call, f"Unknown tool '{call.name}'")

        if plugin.consumes is not None:
            error = self._validate_consumes(call, plugin, context)
            if error is not None:
                return self._error_message(call, error)

        try:
            result = await plugin.execute(call.arguments, context)
        except PluginError as exc:
            return self._error_message(call, exc.message)
        except Exception:  # noqa: BLE001 -- a plugin bug must not crash the loop
            logger.exception("plugin '%s' raised an unhandled exception", call.name)
            return self._error_message(call, f"internal error running '{call.name}'")

        context.prior_results[call.id] = result.data
        context.prior_call_names[call.id] = plugin.name
        return Message(
            role="tool",
            content=result.llm_summary,
            data=result.data,
            tool_call_id=call.id,
            tool_name=call.name,
            is_error=False,
        )

    async def _execute_tool_calls_streaming(
        self,
        tool_calls: list[ToolCall],
        messages: list[Message],
        new_messages: list[Message],
        context: PluginContext,
    ) -> AsyncIterator[LoopStreamEvent]:
        for call in tool_calls:
            plugin = get_plugin(call.name)
            yield ToolSelected(tool_call_id=call.id, name=call.name, arguments=call.arguments)

            # Queue is local to this one call -- no state shared across
            # requests on the long-lived AgentLoop instance. The plugin
            # runs as a task so progress items can be drained and yielded
            # as they arrive instead of being buffered until it finishes;
            # _run_one_call_streaming pushes a None sentinel in its own
            # `finally` (success, failure, or cancellation alike) so this
            # draining loop always terminates.
            queue: asyncio.Queue[str | None] = asyncio.Queue()

            async def on_progress(text: str, _queue: asyncio.Queue = queue) -> None:
                await _queue.put(text)

            task = asyncio.create_task(self._run_one_call_streaming(call, plugin, context, on_progress, queue))
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    yield ToolProgress(tool_call_id=call.id, message=item)
                tool_msg = await task
            finally:
                # Reached on normal completion too (task is already done by
                # then, so this is a no-op) and on this generator being
                # closed mid-call (client disconnect) -- cancels the
                # in-flight plugin call rather than abandoning it.
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(BaseException):
                        await task

            messages.append(tool_msg)
            new_messages.append(tool_msg)
            yield ToolResult(
                tool_call_id=call.id,
                name=call.name,
                is_error=tool_msg.is_error,
                result=tool_msg.data if tool_msg.data is not None else tool_msg.content,
            )

    async def _run_one_call_streaming(
        self,
        call: ToolCall,
        plugin: Plugin | None,
        context: PluginContext,
        on_progress,
        queue: asyncio.Queue,
    ) -> Message:
        try:
            if plugin is None:
                return self._error_message(call, f"Unknown tool '{call.name}'")

            if plugin.consumes is not None:
                error = self._validate_consumes(call, plugin, context)
                if error is not None:
                    return self._error_message(call, error)

            try:
                result = await plugin.execute(call.arguments, context, on_progress=on_progress)
            except PluginError as exc:
                return self._error_message(call, exc.message)
            except Exception:  # noqa: BLE001 -- a plugin bug must not crash the loop
                logger.exception("plugin '%s' raised an unhandled exception", call.name)
                return self._error_message(call, f"internal error running '{call.name}'")

            context.prior_results[call.id] = result.data
            context.prior_call_names[call.id] = plugin.name
            return Message(
                role="tool",
                content=result.llm_summary,
                data=result.data,
                tool_call_id=call.id,
                tool_name=call.name,
                is_error=False,
            )
        finally:
            await queue.put(None)

    @staticmethod
    def _validate_consumes(call: ToolCall, plugin: Plugin, context: PluginContext) -> str | None:
        source_call_id = call.arguments.get(SOURCE_CALL_ID_ARG)
        if not source_call_id:
            return f"'{plugin.name}' requires a '{SOURCE_CALL_ID_ARG}' argument referencing a prior '{plugin.consumes}' call"
        if source_call_id not in context.prior_results:
            return f"'{SOURCE_CALL_ID_ARG}' \"{source_call_id}\" does not refer to a completed call in this turn"
        actual_source = context.prior_call_names.get(source_call_id)
        if actual_source != plugin.consumes:
            return (
                f"'{SOURCE_CALL_ID_ARG}' \"{source_call_id}\" refers to a '{actual_source}' call, "
                f"but '{plugin.name}' requires a '{plugin.consumes}' call"
            )
        return None

    @staticmethod
    def _error_message(call: ToolCall, message: str) -> Message:
        return Message(
            role="tool",
            content=f"Error: {message}",
            data=None,
            tool_call_id=call.id,
            tool_name=call.name,
            is_error=True,
        )

    @staticmethod
    def _tool_schemas() -> list[ToolSchema]:
        return [ToolSchema(name=p.name, description=p.description, input_schema=p.input_schema) for p in all_plugins()]
