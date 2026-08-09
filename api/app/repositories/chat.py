import json
from uuid import UUID

import asyncpg

from app.agent.messages import Message, ToolCall


async def create_session(conn: asyncpg.Connection) -> UUID:
    return await conn.fetchval("INSERT INTO chat_sessions DEFAULT VALUES RETURNING session_id")


async def session_exists(conn: asyncpg.Connection, session_id: UUID) -> bool:
    return bool(await conn.fetchval("SELECT 1 FROM chat_sessions WHERE session_id = $1", session_id))


async def load_history(conn: asyncpg.Connection, session_id: UUID) -> list[Message]:
    rows = await conn.fetch(
        """
        SELECT role, content, tool_calls, tool_call_id, tool_name, is_error, data
        FROM chat_messages
        WHERE session_id = $1
        ORDER BY message_id
        """,
        session_id,
    )
    messages = []
    for r in rows:
        tool_calls_json = json.loads(r["tool_calls"]) if r["tool_calls"] else []
        messages.append(
            Message(
                role=r["role"],
                content=r["content"],
                tool_calls=[ToolCall(**tc) for tc in tool_calls_json],
                tool_call_id=r["tool_call_id"],
                tool_name=r["tool_name"],
                is_error=r["is_error"],
                data=json.loads(r["data"]) if r["data"] else None,
            )
        )
    return messages


async def append_messages(conn: asyncpg.Connection, session_id: UUID, messages: list[Message]) -> None:
    async with conn.transaction():
        for m in messages:
            tool_calls_json = (
                json.dumps([{"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in m.tool_calls])
                if m.tool_calls
                else None
            )
            data_json = json.dumps(m.data, default=str) if m.data is not None else None
            await conn.execute(
                """
                INSERT INTO chat_messages (session_id, role, content, tool_calls, tool_call_id, tool_name, is_error, data)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                session_id,
                m.role,
                m.content,
                tool_calls_json,
                m.tool_call_id,
                m.tool_name,
                m.is_error,
                data_json,
            )
        await conn.execute("UPDATE chat_sessions SET updated_at = now() WHERE session_id = $1", session_id)
