from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import db as dbq
from ..hooks import dispatch_event
from ..presets import PRESETS


class Rule(BaseModel):
    priority: str = "default"
    extra_tags: list[str] = []


class EndpointIn(BaseModel):
    name: str = Field(min_length=1)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    ntfy_topic: str = Field(min_length=1)
    ntfy_token: str | None = None
    ntfy_server: str | None = None
    title_template: str = ""
    message_template: str = "{payload}"
    level_field: str = ""
    rules: dict[str, Rule] = {}
    default_priority: str = "default"
    tags: str = ""
    enabled: bool = True


PUBLIC_FIELDS = ("id", "slug", "name", "enabled", "ntfy_topic", "ntfy_server",
                 "title_template", "message_template", "level_field", "rules",
                 "default_priority", "tags", "created_at", "updated_at")


def public_endpoint(row: dict) -> dict:
    out = {k: row[k] for k in PUBLIC_FIELDS}
    token = row["ntfy_token"] or ""
    out["ntfy_token_set"] = bool(token)
    out["ntfy_token_hint"] = ("…" + token[-4:]) if token else ""
    return out


def _payload(body: EndpointIn) -> dict:
    data = body.model_dump()
    data["rules"] = {k.upper(): v for k, v in data["rules"].items()}
    return data


def router(state, require_auth) -> APIRouter:
    r = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

    @r.get("/presets")
    async def presets():
        return PRESETS

    @r.get("/endpoints")
    async def list_all():
        return [public_endpoint(e) for e in await dbq.list_endpoints(state.db)]

    @r.post("/endpoints", status_code=201)
    async def create(body: EndpointIn):
        if await dbq.get_endpoint_by_slug(state.db, body.slug):
            raise HTTPException(status_code=409, detail="slug already exists")
        data = _payload(body)
        data["ntfy_token"] = data["ntfy_token"] or ""
        return public_endpoint(await dbq.create_endpoint(state.db, data))

    @r.get("/endpoints/{endpoint_id}")
    async def get_one(endpoint_id: int):
        row = await dbq.get_endpoint(state.db, endpoint_id)
        if row is None:
            raise HTTPException(status_code=404, detail="endpoint not found")
        return public_endpoint(row)

    @r.put("/endpoints/{endpoint_id}")
    async def update(endpoint_id: int, body: EndpointIn):
        if await dbq.get_endpoint(state.db, endpoint_id) is None:
            raise HTTPException(status_code=404, detail="endpoint not found")
        other = await dbq.get_endpoint_by_slug(state.db, body.slug)
        if other and other["id"] != endpoint_id:
            raise HTTPException(status_code=409, detail="slug already exists")
        data = _payload(body)
        if body.ntfy_token is None:
            data.pop("ntfy_token")  # absent/null token = keep the stored secret
        return public_endpoint(await dbq.update_endpoint(state.db, endpoint_id, data))

    @r.delete("/endpoints/{endpoint_id}", status_code=204)
    async def delete(endpoint_id: int):
        if not await dbq.delete_endpoint(state.db, endpoint_id):
            raise HTTPException(status_code=404, detail="endpoint not found")

    @r.post("/endpoints/{endpoint_id}/test")
    async def send_test(endpoint_id: int, request: Request):
        row = await dbq.get_endpoint(state.db, endpoint_id)
        if row is None:
            raise HTTPException(status_code=404, detail="endpoint not found")
        event = {"body": "Test notification from ntfy webhook gateway"}
        source_ip = request.client.host if request.client else ""
        return await dispatch_event(state, row, event, source_ip, "(test)")

    return r
