from datetime import datetime, timedelta, timezone

from app import db as dbq
from app.main import run_retention_once, seed_legacy


async def test_seed_legacy_creates_endpoint_once(db, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "omada-topic")
    monkeypatch.setenv("NTFY_AUTH_TOKEN", "tk_legacy")
    monkeypatch.setenv("NTFY_HOST_URL", "https://ntfy.example.com")
    await seed_legacy(db)
    ep = await dbq.get_endpoint_by_slug(db, "omada")
    assert ep is not None
    assert ep["ntfy_topic"] == "omada-topic" and ep["ntfy_token"] == "tk_legacy"
    assert ep["level_field"] == "event.level|level"
    assert await dbq.get_setting(db, "ntfy_server") == "https://ntfy.example.com"
    # Idempotent: even after the endpoint is deleted, seeding never re-runs.
    await dbq.delete_endpoint(db, ep["id"])
    await seed_legacy(db)
    assert await dbq.get_endpoint_by_slug(db, "omada") is None


async def test_seed_legacy_noop_without_env(db, monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    monkeypatch.delenv("NTFY_AUTH_TOKEN", raising=False)
    await seed_legacy(db)
    assert await dbq.list_endpoints(db) == []


async def test_retention_purges_old_rows(state, sample_endpoint_data):
    ep = await dbq.create_endpoint(state.db, sample_endpoint_data)
    old_id = await dbq.record_delivery(state.db, endpoint_id=ep["id"], status="delivered")
    stale = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(timespec="seconds")
    await state.db.execute("UPDATE deliveries SET received_at=? WHERE id=?", (stale, old_id))
    await state.db.commit()
    fresh_id = await dbq.record_delivery(state.db, endpoint_id=ep["id"], status="delivered")
    await run_retention_once(state)
    assert await dbq.get_delivery(state.db, old_id) is None
    assert await dbq.get_delivery(state.db, fresh_id) is not None


async def test_apps_are_isolated(state):
    from httpx import ASGITransport, AsyncClient
    from app.api import create_admin_app
    from app.hooks import create_hooks_app
    hooks_transport = ASGITransport(app=create_hooks_app(state))
    admin_transport = ASGITransport(app=create_admin_app(state))
    async with AsyncClient(transport=hooks_transport, base_url="http://x") as hooks_client:
        assert (await hooks_client.get("/api/endpoints")).status_code == 404
        assert (await hooks_client.get("/api/health")).status_code == 404
    async with AsyncClient(transport=admin_transport, base_url="http://x") as admin_client:
        # 404 before the SPA exists; 405 once StaticFiles is mounted at "/"
        # (static files reject POST). Either way: no webhook handling here.
        assert (await admin_client.post("/hooks/anything", json={})).status_code in (404, 405)
