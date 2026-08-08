import pytest


@pytest.mark.asyncio
async def test_list_servers(client):
    resp = await client.get("/servers")
    assert resp.status_code == 200
    body = resp.json()
    assert body["page"]["total"] == 2
    assert {s["server_id"] for s in body["items"]} == {"srv_1", "srv_2"}


@pytest.mark.asyncio
async def test_list_servers_pagination_limit_over_max_is_422(client):
    resp = await client.get("/servers", params={"limit": 500})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_list_servers_pagination_negative_offset_is_422(client):
    resp = await client.get("/servers", params={"offset": -1})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_server(client):
    resp = await client.get("/servers/srv_1")
    assert resp.status_code == 200
    assert resp.json()["server_name"] == "Test Server 1"


@pytest.mark.asyncio
async def test_get_server_not_found(client):
    resp = await client.get("/servers/does-not-exist")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "not_found"
