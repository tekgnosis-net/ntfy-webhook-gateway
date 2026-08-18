import json
import time

from . import db as dbq
from . import ntfy
from .templating import payload_text, render, resolve_first

MAX_BODY = 65536


def parse_events(raw: bytes) -> list[dict]:
    text = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [{"body": text}]
    if isinstance(data, list):
        return [item if isinstance(item, dict) else {"body": str(item)} for item in data]
    if isinstance(data, dict):
        return [data]
    return [{"body": str(data)}]


def build_notification(endpoint: dict, event: dict) -> dict:
    title = render(endpoint["title_template"], event) or endpoint["name"]
    message = render(endpoint["message_template"], event) or payload_text(event)
    priority = endpoint["default_priority"] or "default"
    tags = [t.strip() for t in (endpoint["tags"] or "").split(",") if t.strip()]
    level = resolve_first(event, endpoint["level_field"]) if endpoint["level_field"] else None
    if level is not None:
        rule = (endpoint["rules"] or {}).get(str(level).upper())
        if rule:
            priority = rule.get("priority", priority)
            tags += [t for t in rule.get("extra_tags", []) if t not in tags]
    return {"title": title, "message": message, "priority": priority, "tags": tags}


async def dispatch_event(state, endpoint, event, source_ip, request_body) -> dict:
    notification = build_notification(endpoint, event)
    server = endpoint["ntfy_server"] or await dbq.get_setting(state.db, "ntfy_server", "")
    started = time.monotonic()
    if not server:
        result = ntfy.SendResult(False, None, "no ntfy server configured", 0)
    else:
        result = await ntfy.send(
            state.client, server, endpoint["ntfy_topic"], endpoint["ntfy_token"],
            notification["title"], notification["message"], notification["priority"],
            notification["tags"], retry_delays=state.retry_delays,
        )
    status = "delivered" if result.ok else "failed"
    delivery_id = await dbq.record_delivery(
        state.db, endpoint_id=endpoint["id"], status=status, source_ip=source_ip,
        request_body=request_body, title=notification["title"],
        message=notification["message"], ntfy_status=result.status_code,
        error=result.error, attempts=result.attempts,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return {"status": status, "ntfy_status": result.status_code, "error": result.error,
            "attempts": result.attempts, "delivery_id": delivery_id}
