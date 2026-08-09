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


@dataclass
class AssistantTurn:
    text: str | None
    tool_calls: list[ToolCall]
