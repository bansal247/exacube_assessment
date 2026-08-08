from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import connect_pool, disconnect_pool
from app.errors import register_exception_handlers
from app.routers import activity, channels, health, members, servers


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await connect_pool()
    yield
    await disconnect_pool()


app = FastAPI(title="Discord Analytics API", lifespan=lifespan)
register_exception_handlers(app)

app.include_router(health.router)
app.include_router(servers.router)
app.include_router(channels.router)
app.include_router(members.router)
app.include_router(activity.router)
