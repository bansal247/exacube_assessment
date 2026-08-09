import logging
import time

from asyncpg import Connection
from fastapi import APIRouter, Depends

from app.agent.messages import Message, sum_usage
from app.agent.service import ChatService
from app.routers.deps import get_agent_connection, get_chat_service
from app.schemas.chat import ChatRequest, ChatResponse, ToolCallTrace

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
        call = calls_by_id.get(m.tool_call_id)
        trace.append(
            ToolCallTrace(
                tool_call_id=m.tool_call_id,
                name=m.tool_name or (call.name if call else "unknown"),
                arguments=call.arguments if call else {},
                is_error=m.is_error,
                result=m.data if m.data is not None else m.content,
            )
        )
    return trace
