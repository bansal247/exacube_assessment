from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import Page


class ChatRequest(BaseModel):
    session_id: UUID | None = None
    message: str = Field(min_length=1, max_length=4000)


class ToolCallTrace(BaseModel):
    tool_call_id: str
    name: str
    arguments: dict
    is_error: bool
    result: object
    # Derived from the plugin registry, same as Pin.display_kind -- lets
    # the frontend dispatch on "table"/"chart"/"image"/"file" generically
    # instead of hardcoding a plugin name, the same way pinning already
    # avoids a hardcoded per-type chain. None when the plugin name wasn't
    # found in the registry (e.g. an unknown-tool error) -- there's no
    # display_kind to report for a call that never resolved to a plugin.
    display_kind: str | None = None


class ChatResponse(BaseModel):
    session_id: UUID
    reply: str
    tool_calls: list[ToolCallTrace]
    # Eval-section instrumentation ("log per-turn latency and token cost"):
    # summed across every provider call this turn made.
    latency_ms: float
    input_tokens: int
    output_tokens: int


class SessionSummary(BaseModel):
    session_id: UUID
    created_at: datetime
    updated_at: datetime
    # None for a session that was created but never got a first message
    # (resolve_session() can create one eagerly) -- an empty session isn't
    # an error, just nothing to preview yet.
    preview: str | None


class SessionList(BaseModel):
    items: list[SessionSummary]
    page: Page


class ToolCallSummary(BaseModel):
    id: str
    name: str
    arguments: dict


class SessionMessage(BaseModel):
    # Mirrors agent.messages.Message plus the same display_kind lookup
    # ToolCallTrace gets -- the frontend groups this flat list back into
    # turns itself (a "user" message starts a new turn; the
    # "assistant"/"tool" messages after it belong to that turn), the same
    # shape it already knows how to render from a live /chat response.
    role: Literal["user", "assistant", "tool"]
    content: str | None
    tool_calls: list[ToolCallSummary] = []
    tool_call_id: str | None = None
    tool_name: str | None = None
    is_error: bool = False
    result: object | None = None
    display_kind: str | None = None


class SessionDetail(BaseModel):
    session_id: UUID
    messages: list[SessionMessage]
