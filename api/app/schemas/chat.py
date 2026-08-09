from uuid import UUID

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: UUID | None = None
    message: str = Field(min_length=1, max_length=4000)


class ToolCallTrace(BaseModel):
    tool_call_id: str
    name: str
    arguments: dict
    is_error: bool
    result: object


class ChatResponse(BaseModel):
    session_id: UUID
    reply: str
    tool_calls: list[ToolCallTrace]
