from app import db as dbq


async def _seed(state, sample_endpoint_data):
    ep = await dbq.create_endpoint(state.db, sample_endpoint_data)
    for status in ("delivered", "delivered", "failed", "rejected"):
        await dbq.record_delivery(state.db, endpoint_id=ep["id"], status=status,
                                  title=f"t-{status}", request_body='{"k":"v"}',
                                  message="m", error=None if status == "delivered" else "e")
    return ep


async def test_logs_list_and_detail(admin_client, state, sample_endpoint_data):
    ep = await _seed(state, sample_endpoint_data)
    items = (await admin_client.get("/api/logs")).json()["items"]
    assert len(items) == 4
    assert items[0]["endpoint_name"] == ep["name"]
    assert "request_body" not in items[0]

    failed = (await admin_client.get("/api/logs", params={"status": "failed"})).json()["items"]
    assert len(failed) == 1
    detail = (await admin_client.get(f"/api/logs/{failed[0]['id']}")).json()
    assert detail["request_body"] == '{"k":"v"}' and detail["error"] == "e"
    assert (await admin_client.get("/api/logs/999999")).status_code == 404


async def test_reports_summary(admin_client, state, sample_endpoint_data):
    ep = await _seed(state, sample_endpoint_data)
    summary = (await admin_client.get("/api/reports/summary", params={"range": "24h"})).json()
    row = next(e for e in summary["endpoints"] if e["id"] == ep["id"])
    assert (row["delivered"], row["failed"], row["rejected"], row["total"]) == (2, 1, 1, 4)
    assert row["success_rate"] == 50.0
    assert sum(b["delivered"] + b["failed"] + b["rejected"] for b in summary["buckets"]) == 4
    assert (await admin_client.get("/api/reports/summary", params={"range": "bad"})).status_code == 422


async def test_dashboard(admin_client, state, sample_endpoint_data):
    ep = await _seed(state, sample_endpoint_data)
    data = (await admin_client.get("/api/dashboard")).json()
    assert data["endpoint_count"] == 1 and data["enabled_count"] == 1
    assert data["deliveries_24h"] == 4 and data["failures_24h"] == 2
    assert data["endpoints"][0]["last_at"]
    assert len(data["recent"]) == 4 and data["recent"][0]["endpoint_name"] == ep["name"]
