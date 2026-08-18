import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import uvicorn

from . import config
from . import db as dbq
from .api import create_admin_app
from .hooks import create_hooks_app
from .presets import get_preset
from .state import AppState


async def seed_legacy(db):
    legacy = config.legacy_env()
    if not legacy or await dbq.get_setting(db, "legacy_seeded"):
        return
    if not await dbq.get_endpoint_by_slug(db, "omada"):
        preset = get_preset("omada")
        await dbq.create_endpoint(db, {
            "slug": "omada", "name": "Omada Controller", "enabled": True,
            "ntfy_topic": legacy["topic"], "ntfy_token": legacy["token"],
            "ntfy_server": None,
            "title_template": preset["title_template"],
            "message_template": preset["message_template"],
            "level_field": preset["level_field"], "rules": preset["rules"],
            "default_priority": preset["default_priority"], "tags": preset["tags"],
        })
        if legacy["server"] and not await dbq.get_setting(db, "ntfy_server"):
            await dbq.set_setting(db, "ntfy_server", legacy["server"].rstrip("/"))
    await dbq.set_setting(db, "legacy_seeded", "1")


async def run_retention_once(state):
    days = int(await dbq.get_setting(state.db, "retention_days", "30"))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    await dbq.purge_deliveries(state.db, cutoff)
    await dbq.purge_sessions(state.db)


async def retention_loop(state):
    while True:
        await run_retention_once(state)
        await asyncio.sleep(24 * 3600)


async def run():
    config.data_dir().mkdir(parents=True, exist_ok=True)
    db = await dbq.connect(config.db_path())
    await seed_legacy(db)
    async with httpx.AsyncClient(timeout=15) as client:
        state = AppState(db, client)
        retention = asyncio.create_task(retention_loop(state))
        hooks_server = uvicorn.Server(uvicorn.Config(
            create_hooks_app(state), host="0.0.0.0", port=config.webhook_port(),
            log_level="info"))
        admin_server = uvicorn.Server(uvicorn.Config(
            create_admin_app(state), host="0.0.0.0", port=config.admin_port(),
            log_level="info"))
        try:
            await asyncio.gather(hooks_server.serve(), admin_server.serve())
        finally:
            retention.cancel()
    await db.close()


if __name__ == "__main__":
    asyncio.run(run())
