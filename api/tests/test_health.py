import asyncpg
import pytest

from app.routers import health as health_module


@pytest.mark.asyncio
async def test_health_ok(client, api_role_url, monkeypatch):
    pool = await asyncpg.create_pool(
        api_role_url,
        min_size=1,
        max_size=1,
    )

    monkeypatch.setattr(
        health_module,
        "get_pool",
        lambda: pool,
    )

    try:
        resp = await client.get("/health")

        assert resp.status_code == 200
        assert resp.json() == {
            "status": "ok",
            "database": "reachable",
        }
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_health_db_unreachable_returns_503(client, monkeypatch):
    def raise_not_initialized():
        raise RuntimeError("pool not initialized")

    monkeypatch.setattr(
        health_module,
        "get_pool",
        raise_not_initialized,
    )

    resp = await client.get("/health")

    assert resp.status_code == 503
    assert resp.json()["status"] == "unhealthy"