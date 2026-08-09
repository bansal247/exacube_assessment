from uuid import UUID

import asyncpg

from app.agent.messages import Message
from app.agent.plugins.registry import get_plugin
from app.errors import NotFoundError
from app.repositories import chat as chat_repo
from app.schemas.chat import SessionDetail, SessionList, SessionMessage, SessionSummary, ToolCallSummary
from app.schemas.common import Page


async def list_sessions(conn: asyncpg.Connection, limit: int, offset: int) -> SessionList:
    rows, total = await chat_repo.list_sessions(conn, limit, offset)
    return SessionList(
        items=[SessionSummary(**dict(r)) for r in rows],
        page=Page(total=total, limit=limit, offset=offset),
    )


async def get_session_messages(conn: asyncpg.Connection, session_id: UUID) -> SessionDetail:
    if not await chat_repo.session_exists(conn, session_id):
        raise NotFoundError(f"Chat session '{session_id}' not found")
    history = await chat_repo.load_history(conn, session_id)
    return SessionDetail(session_id=session_id, messages=[_to_session_message(m) for m in history])


def _to_session_message(m: Message) -> SessionMessage:
    plugin = get_plugin(m.tool_name) if m.tool_name else None
    return SessionMessage(
        role=m.role,
        content=m.content,
        tool_calls=[ToolCallSummary(id=tc.id, name=tc.name, arguments=tc.arguments) for tc in m.tool_calls],
        tool_call_id=m.tool_call_id,
        tool_name=m.tool_name,
        is_error=m.is_error,
        result=m.data if m.data is not None else m.content,
        display_kind=plugin.display_kind if plugin else None,
    )
