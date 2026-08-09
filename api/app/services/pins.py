import json
from uuid import UUID

import asyncpg

from app.agent.messages import Message
from app.agent.plugins.base import ArtifactFile, PluginContext, PluginError
from app.agent.plugins.registry import get_plugin
from app.agent.replay import ChainStep, build_chain, replay_chain
from app.errors import BadRequestError, NotFoundError, UpstreamError
from app.repositories import chat as chat_repo
from app.repositories import pins as pins_repo


async def create_pin(conn: asyncpg.Connection, session_id: UUID, tool_call_id: str) -> asyncpg.Record:
    if not await chat_repo.session_exists(conn, session_id):
        raise NotFoundError(f"Chat session '{session_id}' not found")

    history = await chat_repo.load_history(conn, session_id)
    target = _find_tool_message(history, tool_call_id)
    if target is None:
        raise NotFoundError(f"No tool call '{tool_call_id}' found in session '{session_id}'")
    if target.is_error or target.data is None:
        raise BadRequestError(f"Tool call '{tool_call_id}' did not produce a result (it failed)")

    # _find_tool_message only ever returns a role="tool" message, and
    # append_messages always sets tool_name for those -- Message's own type
    # is str | None because it's shared across all three roles, not because
    # this can actually be missing here.
    assert target.tool_name is not None
    plugin = get_plugin(target.tool_name)
    if plugin is None:
        raise BadRequestError(f"Plugin '{target.tool_name}' is no longer registered; cannot pin its result")

    chain = build_chain(history, tool_call_id)
    title = _derive_title(chain, plugin.name)

    return await pins_repo.create_pin(
        conn,
        session_id=session_id,
        source_tool_call_id=tool_call_id,
        plugin_name=plugin.name,
        display_kind=plugin.display_kind,
        title=title,
        call_chain=chain,
        cached_data=target.data,
    )


async def list_pins(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    return await pins_repo.list_pins(conn)


async def delete_pin(conn: asyncpg.Connection, pin_id: UUID) -> None:
    deleted = await pins_repo.delete_pin(conn, pin_id)
    if not deleted:
        raise NotFoundError(f"Pin '{pin_id}' not found")


async def reorder_pins(conn: asyncpg.Connection, ordered_ids: list[UUID]) -> list[asyncpg.Record]:
    existing = await pins_repo.all_pin_ids(conn)
    given = set(ordered_ids)
    if given != existing or len(ordered_ids) != len(given):
        raise BadRequestError(
            "'order' must contain exactly the current set of pin ids, each exactly once",
            details={"expected": [str(i) for i in existing], "received": [str(i) for i in ordered_ids]},
        )
    await pins_repo.reorder_pins(conn, ordered_ids)
    return await pins_repo.list_pins(conn)


async def refresh_pin(conn: asyncpg.Connection, pin_id: UUID) -> asyncpg.Record:
    pin = await pins_repo.get_pin(conn, pin_id)
    if pin is None:
        raise NotFoundError(f"Pin '{pin_id}' not found")

    chain = pins_repo.chain_from_json(pin["call_chain"])
    context = PluginContext(agent_conn=conn)
    try:
        fresh_data = await replay_chain(chain, context)
    except PluginError as exc:
        # Re-running the stored chain is the same trust level as it running
        # live the first time -- each step's own plugin (e.g. query, via
        # sql_safety) re-validates itself; this isn't a separate/weaker path.
        raise UpstreamError(f"Pinned artifact could not be refreshed: {exc.message}") from exc

    return await pins_repo.update_cache(conn, pin_id, fresh_data)


async def download_pin(conn: asyncpg.Connection, pin_id: UUID) -> ArtifactFile:
    pin = await pins_repo.get_pin(conn, pin_id)
    if pin is None:
        raise NotFoundError(f"Pin '{pin_id}' not found")

    plugin = get_plugin(pin["plugin_name"])
    if plugin is None:
        raise BadRequestError(f"Plugin '{pin['plugin_name']}' is no longer registered")

    file = await plugin.to_file(json.loads(pin["cached_data"]))
    if file is None:
        raise BadRequestError(f"'{pin['plugin_name']}' results aren't downloadable as a file")
    return file


def _find_tool_message(history: list[Message], tool_call_id: str) -> Message | None:
    for m in history:
        if m.role == "tool" and m.tool_call_id == tool_call_id:
            return m
    return None


def _derive_title(chain: list[ChainStep], plugin_name: str) -> str:
    target = chain[-1]
    title = target.arguments.get("title")
    if isinstance(title, str) and title.strip():
        return title
    return f"{plugin_name} result"
