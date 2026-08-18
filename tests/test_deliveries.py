from app import db as dbq


async def _seed(db, sample_endpoint_data):
    ep = await dbq.create_endpoint(db, sample_endpoint_data)
    ids = []
    for status, error in (("delivered", None), ("failed", "boom"), ("rejected", "endpoint disabled")):
        ids.append(await dbq.record_delivery(
            db, endpoint_id=ep["id"], status=status, request_body='{"n":1}',
            title=f"t-{status}", message="m", error=error,
        ))
    return ep, ids


async def test_record_and_get(db, sample_endpoint_data):
    ep, ids = await _seed(db, sample_endpoint_data)
    row = await dbq.get_delivery(db, ids[0])
    assert row["status"] == "delivered"
    assert row["endpoint_id"] == ep["id"]
    assert row["received_at"]


async def test_body_truncated_to_64k(db, sample_endpoint_data):
    ep = await dbq.create_endpoint(db, sample_endpoint_data)
    did = await dbq.record_delivery(db, endpoint_id=ep["id"], status="delivered",
                                    request_body="x" * 100_000)
    assert len((await dbq.get_delivery(db, did))["request_body"]) == 65536


async def test_list_filters_and_paging(db, sample_endpoint_data):
    ep, ids = await _seed(db, sample_endpoint_data)
    assert len(await dbq.list_deliveries(db)) == 3
    assert [d["id"] for d in await dbq.list_deliveries(db)] == sorted(ids, reverse=True)
    assert len(await dbq.list_deliveries(db, status="failed")) == 1
    assert len(await dbq.list_deliveries(db, q="t-rejected")) == 1
    assert len(await dbq.list_deliveries(db, endpoint_id=ep["id"] + 1)) == 0
    page = await dbq.list_deliveries(db, before_id=ids[2], limit=1)
    assert page[0]["id"] == ids[1]


async def test_stats_buckets_last_times(db, sample_endpoint_data):
    ep, _ = await _seed(db, sample_endpoint_data)
    stats = await dbq.delivery_stats(db, "2000-01-01T00:00:00+00:00")
    assert {(s["endpoint_id"], s["status"], s["n"]) for s in stats} == {
        (ep["id"], "delivered", 1), (ep["id"], "failed", 1), (ep["id"], "rejected", 1),
    }
    buckets = await dbq.delivery_buckets(db, "2000-01-01T00:00:00+00:00", "%Y-%m-%d")
    assert sum(b["n"] for b in buckets) == 3
    # display-zone bucketing: 23:00 UTC on Jan 1 is Jan 2 in UTC+2 (offset -120)
    await db.execute("UPDATE deliveries SET received_at='2026-01-01T23:00:00+00:00'")
    await db.commit()
    local = await dbq.delivery_buckets(db, "2000-01-01T00:00:00+00:00", "%Y-%m-%d",
                                       offset_minutes=-120)
    assert {b["bucket"] for b in local} == {"2026-01-02"}
    assert ep["id"] in await dbq.last_delivery_times(db)


async def test_purge(db, sample_endpoint_data):
    await _seed(db, sample_endpoint_data)
    assert await dbq.purge_deliveries(db, "2000-01-01T00:00:00+00:00") == 0
    assert await dbq.purge_deliveries(db, "2999-01-01T00:00:00+00:00") == 3
    assert await dbq.list_deliveries(db) == []
