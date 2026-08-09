from collections.abc import AsyncIterator
from uuid import UUID

import asyncpg

from app.agent.loop import AgentLoop, TurnMessages
from app.agent.messages import Message
from app.agent.stream_events import LoopStreamEvent, SessionStarted
from app.errors import NotFoundError
from app.repositories import chat as chat_repo


class ChatService:
    def __init__(self, loop: AgentLoop):
        self._loop = loop

    async def resolve_session(self, conn: asyncpg.Connection, session_id: UUID | None) -> UUID:
        """Create-or-validate the session eagerly, before any streaming
        response has started. Deliberately not folded into
        send_message_streaming() itself: that method is a generator, and a
        generator's body doesn't run at all until first iterated -- by
        which point StreamingResponse has already sent a 200 status header.
        An error raised from inside the generator can't retroactively
        become a 404 on the wire. The router calls this first (a normal
        awaited call, errors propagate through the usual exception-handler
        path with the right status) and only constructs the
        StreamingResponse once it succeeds.
        """
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

    async def send_message_streaming(
        self, conn: asyncpg.Connection, session_id: UUID, user_message: str
    ) -> AsyncIterator[LoopStreamEvent]:
        """Yields stage events as the turn happens. session_id must already
        be resolved (see resolve_session) -- this method assumes a valid
        session and never raises ApiError itself.

        Persistence occurs only on natural completion (when
        run_streaming's terminal TurnMessages arrives) -- if the caller
        stops iterating early (client disconnect, which Starlette surfaces
        as this generator being closed), whatever was in flight is simply
        not persisted. Deliberate: a turn the client never received isn't
        meaningfully "done" from the system's perspective, and Python
        async generators can't yield further once GeneratorExit starts
        propagating, so partial persistence during a forced close isn't
        achievable without a different mechanism than this (e.g. a
        background task decoupled from the response stream) that this
        session didn't build. Resource cleanup (cancelling an in-flight
        plugin call, releasing the DB connection) is unaffected by this and
        is handled regardless -- see loop.py's run_streaming.
        """
        yield SessionStarted(session_id=session_id)

        history = await chat_repo.load_history(conn, session_id)
        stream = self._loop.run_streaming(history, user_message, agent_conn=conn, session_id=session_id)
        async for event in stream:
            if isinstance(event, TurnMessages):
                await chat_repo.append_messages(conn, session_id, event.messages)
            else:
                yield event
