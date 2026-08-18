import httpx
import respx

from app import db as dbq

EP = {
    "name": "Omada", "slug": "omada", "ntfy_topic": "net",
    "ntfy_token": "tk_secret9999", "ntfy_server": "https://n.example",
    "title_template": "{t}", "message_template": "{m}",
    "level_field": "lvl", "rules": {"warn": {"priority": "high", "extra_tags": ["w"]}},
    "default_priority": "default", "tags": "net", "enabled": True,
}


async def test_crud_and_masking(admin_client):
    created = (await admin_client.post("/api/endpoints", json=EP)).json()
    assert created["ntfy_token_set"] is True
    assert created["ntfy_token_hint"] == "…9999"
    assert "ntfy_token" not in created
    assert "WARN" in created["rules"]  # keys uppercased

    assert (await admin_client.post("/api/endpoints", json=EP)).status_code == 409
    bad = {**EP, "slug": "Bad Slug!"}
    assert (await admin_client.post("/api/endpoints", json=bad)).status_code == 422

    listed = (await admin_client.get("/api/endpoints")).json()
    assert len(listed) == 1

    # PUT without token keeps it; empty string clears it
    update = {**EP, "name": "Renamed", "ntfy_token": None}
    row = (await admin_client.put(f"/api/endpoints/{created['id']}", json=update)).json()
    assert row["name"] == "Renamed" and row["ntfy_token_set"] is True
    update["ntfy_token"] = ""
    row = (await admin_client.put(f"/api/endpoints/{created['id']}", json=update)).json()
    assert row["ntfy_token_set"] is False

    assert (await admin_client.delete(f"/api/endpoints/{created['id']}")).status_code == 204
    assert (await admin_client.get(f"/api/endpoints/{created['id']}")).status_code == 404


async def test_presets_listed(admin_client):
    keys = {p["key"] for p in (await admin_client.get("/api/presets")).json()}
    assert {"generic", "omada"} <= keys


@respx.mock
async def test_send_test_notification(admin_client, state):
    created = (await admin_client.post("/api/endpoints", json=EP)).json()
    respx.post("https://n.example/net").mock(return_value=httpx.Response(200))
    outcome = (await admin_client.post(f"/api/endpoints/{created['id']}/test")).json()
    assert outcome["status"] == "delivered"
    assert len(await dbq.list_deliveries(state.db)) == 1


async def test_settings_roundtrip(admin_client):
    settings = (await admin_client.get("/api/settings")).json()
    assert settings["retention_days"] == 30 and settings["webhook_port"] == 5000
    updated = (await admin_client.put(
        "/api/settings", json={"ntfy_server": "https://n.example/", "retention_days": 7},
    )).json()
    assert updated["ntfy_server"] == "https://n.example"  # trailing slash stripped
    assert updated["retention_days"] == 7
    assert (await admin_client.put("/api/settings", json={"retention_days": 0})).status_code == 422


async def test_endpoints_require_auth(admin_client, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "envpass")
    assert (await admin_client.get("/api/endpoints")).status_code == 401
