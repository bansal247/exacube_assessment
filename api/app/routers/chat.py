import logging
import time
from uuid import UUID

from asyncpg import Connection
from fastapi import APIRouter, Depends, Query

from app.agent.messages import Message, sum_usage
from app.agent.plugins.registry import get_plugin
from app.agent.service import ChatService
from app.routers.deps import get_agent_connection, get_chat_service
from app.schemas.chat import ChatRequest, ChatResponse, SessionDetail, SessionList, ToolCallTrace
from app.schemas.common import DEFAULT_LIMIT, MAX_LIMIT
from app.services import chat_sessions as chat_sessions_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def send_message(
    body: ChatRequest,
    conn: Connection = Depends(get_agent_connection),
    service: ChatService = Depends(get_chat_service),
) -> ChatResponse:
    started_at = time.monotonic()
    logger.info(
        "chat turn started",
        extra={"session_id": str(body.session_id) if body.session_id else None, "message_length": len(body.message)},
    )
    session_id, new_messages = await service.send_message(conn, body.session_id, body.message)
    input_tokens, output_tokens = sum_usage(new_messages)
    latency_ms = (time.monotonic() - started_at) * 1000
    logger.info(
        "chat turn completed",
        extra={
            "session_id": str(session_id),
            "tool_call_count": sum(1 for m in new_messages if m.role == "tool"),
            "latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    )
    return ChatResponse(
        session_id=session_id,
        reply=_final_reply(new_messages),
        tool_calls=_tool_call_trace(new_messages),
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


@router.get("/sessions", response_model=SessionList)
async def list_sessions(
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    conn: Connection = Depends(get_agent_connection),
) -> SessionList:
    return await chat_sessions_service.list_sessions(conn, limit, offset)


@router.get("/sessions/{session_id}/messages", response_model=SessionDetail)
async def get_session_messages(
    session_id: UUID,
    conn: Connection = Depends(get_agent_connection),
) -> SessionDetail:
    return await chat_sessions_service.get_session_messages(conn, session_id)


def _final_reply(messages: list[Message]) -> str:
    for m in reversed(messages):
        if m.role == "assistant" and m.content:
            return m.content
    return ""


def _tool_call_trace(messages: list[Message]) -> list[ToolCallTrace]:
    calls_by_id = {tc.id: tc for m in messages if m.role == "assistant" for tc in m.tool_calls}
    trace = []
    for m in messages:
        if m.role != "tool":
            continue
        # append_messages always sets tool_call_id for a role="tool"
        # message -- Message's own type is str | None because it's shared
        # across all three roles, not because this can be missing here.
        assert m.tool_call_id is not None
        call = calls_by_id.get(m.tool_call_id)
        name = m.tool_name or (call.name if call else "unknown")
        plugin = get_plugin(name)
        trace.append(
            ToolCallTrace(
                tool_call_id=m.tool_call_id,
                name=name,
                arguments=call.arguments if call else {},
                is_error=m.is_error,
                result=m.data if m.data is not None else m.content,
                display_kind=plugin.display_kind if plugin else None,
            )
        )
    return trace
