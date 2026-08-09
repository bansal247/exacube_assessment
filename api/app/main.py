from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.anthropic_provider import AnthropicProvider
from app.agent.loop import AgentLoop
from app.agent.openai_provider import OpenAIProvider
from app.agent.plugins.registry import discover_plugins
from app.agent.provider import LLMProvider
from app.agent.service import ChatService
from app.config import settings
from app.db import connect_agent_pool, connect_pool, disconnect_agent_pool, disconnect_pool
from app.errors import register_exception_handlers
from app.logging_config import configure_logging
from app.routers import activity, channels, chat, health, members, pins, servers
from app.tracing import TraceIdMiddleware

# Configured at import time, not inside lifespan -- log lines from module
# import/startup itself (plugin discovery, provider construction) should
# also be JSON, not go out via Python's default unconfigured handler first.
configure_logging(settings.log_level)


def _build_provider() -> LLMProvider:
    if settings.llm_provider == "openai":
        return OpenAIProvider(api_key=settings.openai_api_key, model=settings.agent_model)
    return AnthropicProvider(api_key=settings.anthropic_api_key, model=settings.agent_model)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_pool()
    await connect_agent_pool()

    discover_plugins()
    loop = AgentLoop(provider=_build_provider(), max_tool_retries=settings.agent_max_tool_retries)
    app.state.chat_service = ChatService(loop=loop)

    yield

    await disconnect_pool()
    await disconnect_agent_pool()


app = FastAPI(title="Discord Analytics API", lifespan=lifespan)
app.add_middleware(TraceIdMiddleware)
# Frontend runs as its own container/origin (see docker-compose.yml's
# `frontend` service) -- CORS is the only thing that makes that a separate
# service instead of a same-origin static mount. Origin is configurable
# since the published port can change; "*" isn't used because this API
# also carries chat/pin state, not just public read-only data.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_methods=["*"],
    allow_headers=["*"],
)
register_exception_handlers(app)

app.include_router(health.router)
app.include_router(servers.router)
app.include_router(channels.router)
app.include_router(members.router)
app.include_router(activity.router)
app.include_router(chat.router)
app.include_router(pins.router)
