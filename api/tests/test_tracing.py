import pytest


@pytest.mark.asyncio
async def test_response_includes_generated_trace_id_header(client):
    resp = await client.get("/servers")
    assert resp.status_code == 200
    assert resp.headers["x-trace-id"]  # generated, non-empty


@pytest.mark.asyncio
async def test_incoming_trace_id_is_echoed_back_unchanged(client):
    resp = await client.get("/servers", headers={"X-Trace-Id": "caller-supplied-id"})
    assert resp.headers["x-trace-id"] == "caller-supplied-id"


@pytest.mark.asyncio
async def test_different_requests_get_different_generated_trace_ids(client):
    first = await client.get("/servers")
    second = await client.get("/servers")
    assert first.headers["x-trace-id"] != second.headers["x-trace-id"]
