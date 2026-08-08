import pytest


@pytest.mark.asyncio
async def test_list_channels_for_server(client):
    resp = await client.get("/servers/srv_1/channels")
    assert resp.status_code == 200
    body = resp.json()
    assert body["page"]["total"] == 3
    names = {c["channel_name"] for c in body["items"]}
    assert names == {"general", "random", "Lounge"}


@pytest.mark.asyncio
async def test_list_channels_for_unknown_server_is_404(client):
    resp = await client.get("/servers/does-not-exist/channels")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_channels_for_server_with_no_channels(client):
    resp = await client.get("/servers/srv_2/channels")
    assert resp.status_code == 200
    assert resp.json()["items"] == []
