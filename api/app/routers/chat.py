import dataclasses
import json
from collections.abc import AsyncIterator

from asyncpg import Connection
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.agent.messages import Message
from app.agent.service import ChatService
from app.agent.stream_events import LoopStreamEvent
from app.routers.deps import get_agent_connection, get_chat_service
from app.schemas.chat import ChatRequest, ChatResponse, ToolCallTrace

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def send_message(
    body: ChatRequest,
    conn: Connection = Depends(get_agent_connection),
    service: ChatService = Depends(get_chat_service),
) -> ChatResponse:
    session_id, new_messages = await service.send_message(conn, body.session_id, body.message)
    return ChatResponse(
        session_id=session_id,
        reply=_final_reply(new_messages),
        tool_calls=_tool_call_trace(new_messages),
    )


@router.post("/stream")
async def send_message_streaming(
    body: ChatRequest,
    conn: Connection = Depends(get_agent_connection),
    service: ChatService = Depends(get_chat_service),
) -> StreamingResponse:
    # Resolved (and any 404 raised) *before* the streaming response starts
    # -- see ChatService.resolve_session for why this can't happen inside
    # the generator itself.
    session_id = await service.resolve_session(conn, body.session_id)
    return StreamingResponse(
        _sse_encode(service.send_message_streaming(conn, session_id, body.message)),
        media_type="text/event-stream",
        headers={
            # Disables response buffering on nginx-fronted deployments,
            # which would otherwise silently turn "streaming" into
            # "buffered then sent all at once" and defeat the whole point.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )


async def _sse_encode(events: AsyncIterator[LoopStreamEvent]) -> AsyncIterator[str]:
    async for event in events:
        payload = {"type": type(event).__name__, **dataclasses.asdict(event)}
        yield f"data: {json.dumps(payload, default=str)}\n\n"


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
                # data is the full structured payload (e.g. a chart spec);
                # falls back to content (the error text) when there's no
                # data, i.e. the call failed.
                result=m.data if m.data is not None else m.content,
            )
        )
    return trace
