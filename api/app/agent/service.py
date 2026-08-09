from uuid import UUID

import asyncpg

from app.agent.loop import AgentLoop
from app.agent.messages import Message
from app.errors import NotFoundError
from app.repositories import chat as chat_repo


class ChatService:
    def __init__(self, loop: AgentLoop):
        self._loop = loop

    async def resolve_session(self, conn: asyncpg.Connection, session_id: UUID | None) -> UUID:
        if session_id is None:
            return await chat_repo.create_session(conn)
        if not await chat_repo.session_exists(conn, session_id):
            raise NotFoundError(f"Chat session '{session_id}' not found")
        return session_id

    async def send_message(
        self, conn: asyncpg.Connection, session_id: UUID | None, user_message: str
    ) -> tuple[UUID, list[Message]]:
        session_id = await self.resolve_session(conn, session_id)

        history = await chat_repo.load_history(conn, session_id)
        new_messages = await self._loop.run(history, user_message, agent_conn=conn)
        await chat_repo.append_messages(conn, session_id, new_messages)

        return session_id, new_messages
