import pytest


@pytest.mark.asyncio
async def test_activity_day_server_total_uses_daily_stats(client):
    resp = await client.get("/servers/srv_1/activity", params={"granularity": "day"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["channel_id"] is None
    assert len(body["items"]) == 1
    assert body["items"][0]["message_count"] == 42
    assert body["items"][0]["active_users"] == 3


@pytest.mark.asyncio
async def test_activity_day_per_channel_uses_channel_daily_stats(client):
    resp = await client.get("/servers/srv_1/activity", params={"granularity": "day", "channel_id": "chan_1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["channel_id"] == "chan_1"
    assert len(body["items"]) == 1
    assert body["items"][0]["message_count"] == 30
    assert body["items"][0]["active_users"] == 2


@pytest.mark.asyncio
async def test_activity_hour_computed_from_messages(client):
    resp = await client.get("/servers/srv_1/activity", params={"granularity": "hour", "channel_id": "chan_1"})
    assert resp.status_code == 200
    body = resp.json()
    total_messages = sum(b["message_count"] for b in body["items"])
    assert total_messages == 2


@pytest.mark.asyncio
async def test_activity_channel_not_in_server_is_404(client):
    resp = await client.get("/servers/srv_2/activity", params={"channel_id": "chan_1"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_activity_unknown_server_is_404(client):
    resp = await client.get("/servers/does-not-exist/activity")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_activity_from_after_to_is_400(client):
    resp = await client.get(
        "/servers/srv_1/activity", params={"from": "2026-02-01", "to": "2026-01-01"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"


@pytest.mark.asyncio
async def test_activity_invalid_granularity_is_422(client):
    resp = await client.get("/servers/srv_1/activity", params={"granularity": "week"})
    assert resp.status_code == 422
