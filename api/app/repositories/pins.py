import json
from uuid import UUID

import asyncpg

from app.agent.replay import ChainStep


def _chain_to_json(chain: list[ChainStep]) -> str:
    return json.dumps(
        [{"tool_call_id": s.tool_call_id, "plugin_name": s.plugin_name, "arguments": s.arguments} for s in chain]
    )


def chain_from_json(raw: str) -> list[ChainStep]:
    return [ChainStep(**step) for step in json.loads(raw)]


async def create_pin(
    conn: asyncpg.Connection,
    session_id: UUID,
    source_tool_call_id: str,
    plugin_name: str,
    display_kind: str,
    title: str,
    call_chain: list[ChainStep],
    cached_data,
) -> asyncpg.Record:
    async with conn.transaction():
        next_position = await conn.fetchval("SELECT COALESCE(MAX(position) + 1, 0) FROM pinned_artifacts")
        return await conn.fetchrow(
            """
            INSERT INTO pinned_artifacts (
                session_id, source_tool_call_id, plugin_name, display_kind, title,
                call_chain, cached_data, "position"
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING *
            """,
            session_id,
            source_tool_call_id,
            plugin_name,
            display_kind,
            title,
            _chain_to_json(call_chain),
            json.dumps(cached_data, default=str),
            next_position,
        )


async def list_pins(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    return await conn.fetch('SELECT * FROM pinned_artifacts ORDER BY "position"')


async def get_pin(conn: asyncpg.Connection, pin_id: UUID) -> asyncpg.Record | None:
    return await conn.fetchrow("SELECT * FROM pinned_artifacts WHERE pin_id = $1", pin_id)


async def delete_pin(conn: asyncpg.Connection, pin_id: UUID) -> bool:
    result = await conn.execute("DELETE FROM pinned_artifacts WHERE pin_id = $1", pin_id)
    return result != "DELETE 0"


async def all_pin_ids(conn: asyncpg.Connection) -> set[UUID]:
    rows = await conn.fetch("SELECT pin_id FROM pinned_artifacts")
    return {r["pin_id"] for r in rows}


async def reorder_pins(conn: asyncpg.Connection, ordered_ids: list[UUID]) -> None:
    async with conn.transaction():
        # position has a DEFERRABLE UNIQUE constraint (schema.sql) -- these
        # per-row UPDATEs can pass through duplicate positions transiently,
        # checked only at COMMIT, so no temporary-offset dance is needed.
        await conn.execute("SET CONSTRAINTS pinned_artifacts_position_key DEFERRED")
        for position, pin_id in enumerate(ordered_ids):
            await conn.execute('UPDATE pinned_artifacts SET "position" = $1 WHERE pin_id = $2', position, pin_id)


async def update_cache(conn: asyncpg.Connection, pin_id: UUID, data) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        UPDATE pinned_artifacts SET cached_data = $1, cached_at = now()
        WHERE pin_id = $2
        RETURNING *
        """,
        json.dumps(data, default=str),
        pin_id,
    )
