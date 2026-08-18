from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import auth
from .. import db as dbq
from ..config import webhook_port


class SettingsIn(BaseModel):
    ntfy_server: str | None = None
    retention_days: int | None = Field(default=None, ge=1, le=365)


def router(state, require_auth) -> APIRouter:
    r = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

    async def current() -> dict:
        return {
            "ntfy_server": await dbq.get_setting(state.db, "ntfy_server", ""),
            "retention_days": int(await dbq.get_setting(state.db, "retention_days", "30")),
            "auth_mode": await auth.auth_mode(state.db),
            "webhook_port": webhook_port(),
        }

    @r.get("/settings")
    async def get_settings():
        return await current()

    @r.put("/settings")
    async def put_settings(body: SettingsIn):
        if body.ntfy_server is not None:
            await dbq.set_setting(state.db, "ntfy_server", body.ntfy_server.rstrip("/"))
        if body.retention_days is not None:
            await dbq.set_setting(state.db, "retention_days", str(body.retention_days))
        return await current()

    return r
