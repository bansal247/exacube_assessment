"""Provider-agnostic conversation representation. Both the DB (chat_messages)
and every LLMProvider implementation translate to/from this shape, so the
agent loop and session storage never need to know which provider is in use.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["user", "assistant", "tool"]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Message:
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Only set on role="tool" messages -- which call this is the result of.
    tool_call_id: str | None = None
    tool_name: str | None = None
    is_error: bool = False
    # Full structured plugin output (PluginResult.data) on success. Separate
    # from `content`, which holds the short summary that's actually
    # replayed to the LLM -- see schema.sql's chat_messages.data comment.
    data: Any = None
    # Only set on role="assistant" messages -- the provider's own reported
    # token usage for the generate() call that produced this message. Not
    # persisted (chat_messages has no column for it -- these are Eval-section
    # instrumentation, summed by the router per turn, not conversation state);
    # carried on Message purely so the router can sum them across a turn's
    # assistant messages without AgentLoop.run()'s return type changing.
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class AssistantTurn:
    text: str | None
    tool_calls: list[ToolCall]
    input_tokens: int | None = None
    output_tokens: int | None = None


def sum_usage(messages: list[Message]) -> tuple[int, int]:
    """Total (input_tokens, output_tokens) across a turn's assistant
    messages -- a multi-round turn (query then chart, say) made more than
    one provider call, each with its own usage.
    """
    input_total = sum(m.input_tokens or 0 for m in messages if m.role == "assistant")
    output_total = sum(m.output_tokens or 0 for m in messages if m.role == "assistant")
    return input_total, output_total
