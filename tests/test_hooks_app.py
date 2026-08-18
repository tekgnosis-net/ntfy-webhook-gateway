import httpx
import respx

from app import db as dbq


async def test_health(hooks_client):
    response = await hooks_client.get("/health")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


async def test_unknown_slug_404_no_log(hooks_client, state):
    response = await hooks_client.post("/hooks/nope", json={"a": 1})
    assert response.status_code == 404
    assert await dbq.list_deliveries(state.db) == []


@respx.mock
async def test_accepts_and_dispatches(hooks_client, state, sample_endpoint_data):
    await dbq.create_endpoint(state.db, sample_endpoint_data)
    respx.post("https://ntfy.example.com/alerts").mock(return_value=httpx.Response(200))
    response = await hooks_client.post("/hooks/test-hook",
                                       json=[{"level": "WARN", "msg": "a"}, {"msg": "b"}])
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "events": 2}
    await state.drain()
    rows = await dbq.list_deliveries(state.db)
    assert len(rows) == 2 and {r["status"] for r in rows} == {"delivered"}


@respx.mock
async def test_non_json_body_accepted(hooks_client, state, sample_endpoint_data):
    await dbq.create_endpoint(state.db, sample_endpoint_data)
    respx.post("https://ntfy.example.com/alerts").mock(return_value=httpx.Response(200))
    response = await hooks_client.post("/hooks/test-hook", content=b"plain alert")
    assert response.status_code == 202
    await state.drain()
    row = (await dbq.list_deliveries(state.db))[0]
    assert row["message"] == "plain alert"


async def test_disabled_endpoint_rejected(hooks_client, state, sample_endpoint_data):
    await dbq.create_endpoint(state.db, {**sample_endpoint_data, "enabled": False})
    response = await hooks_client.post("/hooks/test-hook", json={"a": 1})
    assert response.status_code == 404
    row = (await dbq.list_deliveries(state.db))[0]
    assert row["status"] == "rejected" and row["error"] == "endpoint disabled"


@respx.mock
async def test_legacy_alias_routes_to_omada_slug(hooks_client, state, sample_endpoint_data):
    await dbq.create_endpoint(state.db, {**sample_endpoint_data, "slug": "omada"})
    respx.post("https://ntfy.example.com/alerts").mock(return_value=httpx.Response(200))
    response = await hooks_client.post("/omada-webhook", json={"level": "WARN", "msg": "x"})
    assert response.status_code == 202
    await state.drain()
    assert len(await dbq.list_deliveries(state.db)) == 1
