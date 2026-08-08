import pytest


@pytest.mark.asyncio
async def test_list_members_default_sort_is_messages_sent_desc(client):
    resp = await client.get("/servers/srv_1/members")
    assert resp.status_code == 200
    usernames = [m["username"] for m in resp.json()["items"]]
    assert usernames == ["carol", "alice", "bob"]


@pytest.mark.asyncio
async def test_list_members_sort_asc(client):
    resp = await client.get("/servers/srv_1/members", params={"sort_by": "messages_sent", "order": "asc"})
    usernames = [m["username"] for m in resp.json()["items"]]
    assert usernames == ["bob", "alice", "carol"]


@pytest.mark.asyncio
async def test_list_members_invalid_sort_by_is_422(client):
    resp = await client.get("/servers/srv_1/members", params={"sort_by": "password"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_members_unknown_server_is_404(client):
    resp = await client.get("/servers/does-not-exist/members")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_members_roles_parsed_as_array(client):
    resp = await client.get("/servers/srv_1/members", params={"sort_by": "messages_sent", "order": "desc"})
    carol = resp.json()["items"][0]
    assert carol["username"] == "carol"
    assert carol["roles"] == ["admin"]
    assert carol["is_owner"] is True
