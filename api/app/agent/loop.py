"""The core agent loop: decide -> call tool(s) -> observe -> retry on
failure (bounded) -> respond, or chain into another tool call. Provider- and
plugin-agnostic -- it only knows the Message/ToolCall shapes and the Plugin
contract, never Anthropic or any specific plugin by name.

A streaming variant (run_streaming(), SSE stage events) was built and then
removed -- it worked for the happy path, but a real production run
surfaced a FastAPI/Starlette gotcha (a `Depends()`-provided DB connection
gets released back to the pool as soon as the route handler *returns a
StreamingResponse object*, not when the response finishes sending, so the
response body -- which needed that connection -- crashed with "connection
has been released back to the pool" the moment it tried to use it). Fixing
it properly meant manually managing connection acquisition/release around
the whole streamed generator instead of relying on Depends(), which was
more surface area than there was time for in this pass. Cut deliberately,
not silently -- see README "What I'd do differently."
"""

import logging

import asyncpg

from app.agent.messages import Message, ToolCall
from app.agent.plugins.base import SOURCE_CALL_ID_ARG, Plugin, PluginContext, PluginError
from app.agent.plugins.registry import all_plugins, get_plugin
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.provider import LLMProvider, ToolSchema

logger = logging.getLogger(__name__)

# Hard ceiling on total provider calls in one turn, independent of the
# configurable failure-retry budget -- guards against a pathological case
# where every tool call *succeeds* but the model just keeps chaining
# indefinitely (cost/latency runaway, not a correctness bound).
MAX_ITERATIONS = 8


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

        failed_rounds = 0
        # One context for the whole turn, not one per round -- a model that
        # calls query, sees the result, then calls chart in a *follow-up*
        # message (the common case) needs chart's execute() to still see
        # query's result. Scoping this per-round would silently break that.
        # Seeded from `history`, not empty -- see _context_from_history for
        # why: without this, chaining across separate /chat turns (not just
        # separate rounds within one turn) is silently impossible, no
        # matter what tool_call_id the model references.
        context = self._context_from_history(history, agent_conn)

        for iteration in range(MAX_ITERATIONS):
            # Once the retry budget is exhausted, stop offering tools at all
            # -- the model is forced to answer in prose (graceful surrender)
            # instead of retrying a broken call indefinitely.
            # Recomputed every round (not built once before the loop): a
            # consuming plugin's schema embeds the *actual* valid
            # source_call_id candidates available right now, which only
            # exist once an upstream call has actually happened this turn
            # -- see _tool_schemas().
            offer_tools = self._tool_schemas(context) if failed_rounds <= self._max_tool_retries else []

            logger.info(
                "provider call started",
                extra={"round": iteration, "message_count": len(messages), "tools_offered": [t.name for t in offer_tools]},
            )
            turn = await self._provider.generate(SYSTEM_PROMPT, messages, offer_tools)
            logger.info(
                "provider call completed",
                extra={
                    "round": iteration,
                    "has_tool_calls": bool(turn.tool_calls),
                    "tool_calls_requested": [{"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in turn.tool_calls],
                    "text_length": len(turn.text or ""),
                    "input_tokens": turn.input_tokens,
                    "output_tokens": turn.output_tokens,
                },
            )

            if not turn.tool_calls:
                assistant_msg = Message(role="assistant", content=turn.text, tool_calls=[], input_tokens=turn.input_tokens, output_tokens=turn.output_tokens)
                messages.append(assistant_msg)
                new_messages.append(assistant_msg)
                return new_messages

            assistant_msg = Message(role="assistant", content=turn.text, tool_calls=turn.tool_calls, input_tokens=turn.input_tokens, output_tokens=turn.output_tokens)
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
        logger.info("tool selected", extra={"tool_call_id": call.id, "tool_name": call.name, "arguments": call.arguments})

        if plugin is None:
            logger.warning("unknown tool requested", extra={"tool_call_id": call.id, "tool_name": call.name})
            return self._error_message(call, f"Unknown tool '{call.name}'")

        if plugin.consumes is not None:
            error = self._validate_consumes(call, plugin, context)
            if error is not None:
                logger.warning(
                    "consumes validation failed",
                    extra={"tool_call_id": call.id, "tool_name": call.name, "reason": error},
                )
                return self._error_message(call, error)

        try:
            result = await plugin.execute(call.arguments, context)
        except PluginError as exc:
            logger.warning(
                "plugin returned an error", extra={"tool_call_id": call.id, "tool_name": call.name, "error": exc.message}
            )
            return self._error_message(call, exc.message)
        except Exception:  # noqa: BLE001 -- a plugin bug must not crash the loop
            logger.exception("plugin '%s' raised an unhandled exception", call.name)
            return self._error_message(call, f"internal error running '{call.name}'")

        context.prior_results[call.id] = result.data
        context.prior_call_names[call.id] = plugin.name
        logger.info(
            "tool completed",
            extra={"tool_call_id": call.id, "tool_name": call.name, "is_error": False, "summary": result.llm_summary[:200]},
        )
        return Message(
            role="tool",
            content=result.llm_summary,
            data=result.data,
            tool_call_id=call.id,
            tool_name=call.name,
            is_error=False,
        )

    @staticmethod
    def _validate_consumes(call: ToolCall, plugin: Plugin, context: PluginContext) -> str | None:
        # Lists the *real* candidate ids in the error text -- found via a
        # live run where the model repeated the identical fabricated id
        # ("query_1") on both retries rather than trying something new.
        # Telling it what's valid, not just that it was wrong, gives a
        # retry a concrete string to copy instead of another guess.
        candidates = [cid for cid, name in context.prior_call_names.items() if name == plugin.consumes]
        hint = f" Valid '{plugin.consumes}' call ids this turn: {candidates}." if candidates else ""

        source_call_id = call.arguments.get(SOURCE_CALL_ID_ARG)
        if not source_call_id:
            return (
                f"'{plugin.name}' requires a '{SOURCE_CALL_ID_ARG}' argument referencing a prior "
                f"'{plugin.consumes}' call.{hint}"
            )
        if source_call_id not in context.prior_results:
            return f"'{SOURCE_CALL_ID_ARG}' \"{source_call_id}\" does not refer to a completed call in this turn.{hint}"
        actual_source = context.prior_call_names.get(source_call_id)
        if actual_source != plugin.consumes:
            return (
                f"'{SOURCE_CALL_ID_ARG}' \"{source_call_id}\" refers to a '{actual_source}' call, "
                f"but '{plugin.name}' requires a '{plugin.consumes}' call.{hint}"
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
    def _tool_schemas(context: PluginContext) -> list[ToolSchema]:
        """Builds each round, not once per turn -- a plugin that `consumes`
        another's output gets the *actual* current candidate ids spliced
        into its source_call_id property description, not just a generic
        "provide an id" hint. This is the proactive counterpart to
        _validate_consumes's reactive error message: found from a live run
        where the model repeated the identical fabricated id twice even
        after the error message named the real one -- showing it the real
        id *before* it guesses, not just after, is the more complete fix.
        """
        schemas = []
        for p in all_plugins():
            input_schema = p.input_schema
            if p.consumes is not None:
                input_schema = AgentLoop._with_source_call_id_hint(input_schema, p.consumes, context)
            schemas.append(ToolSchema(name=p.name, description=p.description, input_schema=input_schema))
        return schemas

    @staticmethod
    def _with_source_call_id_hint(input_schema: dict, consumes: str, context: PluginContext) -> dict:
        candidates = [cid for cid, name in context.prior_call_names.items() if name == consumes]
        if candidates:
            hint = (
                f" REAL IDS AVAILABLE RIGHT NOW: {candidates}. You MUST copy one of these exact "
                f"strings -- never invent a new id such as 'query_1'."
            )
        else:
            hint = f" No '{consumes}' call has completed yet this turn -- call '{consumes}' first, then use its real id here."

        # Never mutate `input_schema` in place -- it's the plugin's own
        # class-level dict, shared across every request this long-lived
        # AgentLoop instance ever handles. Build fresh copies of exactly
        # the two levels being changed (the schema dict and the properties
        # dict), leave everything else (including nested dicts we don't
        # touch) referencing the original, shared objects.
        properties = {**input_schema.get("properties", {})}
        source_prop = properties.get(SOURCE_CALL_ID_ARG)
        if source_prop is not None:
            properties[SOURCE_CALL_ID_ARG] = {**source_prop, "description": source_prop.get("description", "") + hint}
        return {**input_schema, "properties": properties}

    @staticmethod
    def _context_from_history(history: list[Message], agent_conn: asyncpg.Connection) -> PluginContext:
        """Seeds prior_results/prior_call_names from persisted history, not
        just this turn's own calls -- the fix for a real bug: without this,
        "chart it" in a follow-up message referencing a `query` call from an
        *earlier* /chat turn always failed validation, no matter what
        tool_call_id the model used, because prior_results started empty
        every turn. Every persisted tool Message already carries what's
        needed (tool_call_id, tool_name, data); only non-error ones are
        replayed in, since a failed call produced no usable result to chain
        from.
        """
        context = PluginContext(agent_conn=agent_conn)
        for m in history:
            if m.role == "tool" and not m.is_error and m.tool_call_id and m.tool_name:
                context.prior_results[m.tool_call_id] = m.data
                context.prior_call_names[m.tool_call_id] = m.tool_name
        return context
