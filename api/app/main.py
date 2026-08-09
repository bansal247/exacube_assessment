from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.agent.anthropic_provider import AnthropicProvider
from app.agent.loop import AgentLoop
from app.agent.plugins.registry import discover_plugins
from app.agent.service import ChatService
from app.config import settings
from app.db import connect_agent_pool, connect_pool, disconnect_agent_pool, disconnect_pool
from app.errors import register_exception_handlers
from app.routers import activity, channels, chat, health, members, pins, servers


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_pool()
    await connect_agent_pool()

    discover_plugins()
    provider = AnthropicProvider(api_key=settings.anthropic_api_key, model=settings.agent_model)
    loop = AgentLoop(provider=provider, max_tool_retries=settings.agent_max_tool_retries)
    app.state.chat_service = ChatService(loop=loop)

    yield

    await disconnect_pool()
    await disconnect_agent_pool()


app = FastAPI(title="Discord Analytics API", lifespan=lifespan)
register_exception_handlers(app)

app.include_router(health.router)
app.include_router(servers.router)
app.include_router(channels.router)
app.include_router(members.router)
app.include_router(activity.router)
app.include_router(chat.router)
app.include_router(pins.router)
