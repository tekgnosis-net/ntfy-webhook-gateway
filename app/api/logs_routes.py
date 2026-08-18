from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import db as dbq

LIST_FIELDS = ("id", "received_at", "endpoint_id", "status", "title",
               "ntfy_status", "attempts", "duration_ms")
RANGES = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}


def _since(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) - delta).isoformat(timespec="seconds")


def _list_item(row: dict, names: dict) -> dict:
    return {**{k: row[k] for k in LIST_FIELDS},
            "endpoint_name": names.get(row["endpoint_id"], "?")}


def router(state, require_auth) -> APIRouter:
    r = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

    async def endpoint_names() -> dict:
        return {e["id"]: e["name"] for e in await dbq.list_endpoints(state.db)}

    @r.get("/logs")
    async def logs(endpoint_id: int | None = None, status: str | None = None,
                   q: str | None = None, before_id: int | None = None,
                   limit: int = Query(default=50, le=200)):
        rows = await dbq.list_deliveries(state.db, endpoint_id=endpoint_id,
                                         status=status, q=q, before_id=before_id,
                                         limit=limit)
        names = await endpoint_names()
        return {"items": [_list_item(row, names) for row in rows]}

    @r.get("/logs/{delivery_id}")
    async def log_detail(delivery_id: int):
        row = await dbq.get_delivery(state.db, delivery_id)
        if row is None:
            raise HTTPException(status_code=404, detail="delivery not found")
        return row

    @r.get("/reports/summary")
    async def summary(range: str = "7d", tz_offset: int = 0):
        delta = RANGES.get(range)
        if delta is None:
            raise HTTPException(status_code=422, detail="range must be one of 24h, 7d, 30d")
        since = _since(delta)
        per_endpoint = {}
        for row in await dbq.delivery_stats(state.db, since):
            agg = per_endpoint.setdefault(row["endpoint_id"],
                                          {"delivered": 0, "failed": 0, "rejected": 0})
            agg[row["status"]] = row["n"]
        endpoints = []
        for e in await dbq.list_endpoints(state.db):
            agg = per_endpoint.get(e["id"], {"delivered": 0, "failed": 0, "rejected": 0})
            total = sum(agg.values())
            endpoints.append({"id": e["id"], "name": e["name"], **agg, "total": total,
                              "success_rate": round(100 * agg["delivered"] / total, 1) if total else None})
        fmt = "%Y-%m-%dT%H:00" if range == "24h" else "%Y-%m-%d"
        buckets = {}
        for row in await dbq.delivery_buckets(state.db, since, fmt,
                                              offset_minutes=tz_offset):
            b = buckets.setdefault(row["bucket"], {"bucket": row["bucket"],
                                                   "delivered": 0, "failed": 0, "rejected": 0})
            b[row["status"]] = row["n"]
        return {"range": range, "since": since, "endpoints": endpoints,
                "buckets": list(buckets.values())}

    @r.get("/dashboard")
    async def dashboard():
        since = _since(timedelta(hours=24))
        per_endpoint = {}
        for row in await dbq.delivery_stats(state.db, since):
            agg = per_endpoint.setdefault(row["endpoint_id"],
                                          {"delivered": 0, "failed": 0, "rejected": 0})
            agg[row["status"]] = row["n"]
        last_times = await dbq.last_delivery_times(state.db)
        all_endpoints = await dbq.list_endpoints(state.db)
        endpoints = [{"id": e["id"], "name": e["name"], "enabled": e["enabled"],
                      **per_endpoint.get(e["id"], {"delivered": 0, "failed": 0, "rejected": 0}),
                      "last_at": last_times.get(e["id"])} for e in all_endpoints]
        names = {e["id"]: e["name"] for e in all_endpoints}
        recent = [_list_item(row, names)
                  for row in await dbq.list_deliveries(state.db, limit=10)]
        return {
            "endpoint_count": len(all_endpoints),
            "enabled_count": sum(1 for e in all_endpoints if e["enabled"]),
            "deliveries_24h": sum(sum(a.values()) for a in per_endpoint.values()),
            "failures_24h": sum(a["failed"] + a["rejected"] for a in per_endpoint.values()),
            "endpoints": endpoints,
            "recent": recent,
        }

    return r
