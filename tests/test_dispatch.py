import httpx
import respx

from app import db as dbq
from app import ntfy
from app.hooks import MAX_BODY, build_notification, dispatch_event, parse_events


def test_parse_events_shapes():
    assert parse_events(b'{"a": 1}') == [{"a": 1}]
    assert parse_events(b'[{"a": 1}, {"b": 2}]') == [{"a": 1}, {"b": 2}]
    assert parse_events(b"[1, 2]") == [{"body": "1"}, {"body": "2"}]
    assert parse_events(b"plain text") == [{"body": "plain text"}]
    assert parse_events(b'"scalar"') == [{"body": "scalar"}]


def test_build_notification_rules_and_fallbacks(sample_endpoint_data):
    endpoint = {**sample_endpoint_data, "id": 1}
    notif = build_notification(endpoint, {"level": "warn", "msg": "disk"})
    assert notif == {"title": "T: warn", "message": "disk",
                     "priority": "high", "tags": ["webhook", "warning"]}

    # no rule match -> default priority, base tags only
    notif = build_notification(endpoint, {"level": "INFO", "msg": "ok"})
    assert notif["priority"] == "default" and notif["tags"] == ["webhook"]

    # empty renders fall back to endpoint name / payload text
    empty = {**endpoint, "title_template": "", "message_template": "{missing}"}
    notif = build_notification(empty, {"a": 1})
    assert notif["title"] == "Test Hook" and '"a": 1' in notif["message"]


@respx.mock
async def test_dispatch_records_success(state, sample_endpoint_data):
    endpoint = await dbq.create_endpoint(state.db, sample_endpoint_data)
    respx.post("https://ntfy.example.com/alerts").mock(return_value=httpx.Response(200))
    outcome = await dispatch_event(state, endpoint, {"level": "WARN", "msg": "hi"}, "1.2.3.4", "{}")
    assert outcome["status"] == "delivered" and outcome["ntfy_status"] == 200
    row = await dbq.get_delivery(state.db, outcome["delivery_id"])
    assert row["status"] == "delivered" and row["source_ip"] == "1.2.3.4"
    assert row["title"] == "T: WARN" and row["message"] == "hi"


@respx.mock
async def test_dispatch_uses_global_server_fallback(state, sample_endpoint_data):
    data = {**sample_endpoint_data, "ntfy_server": None}
    endpoint = await dbq.create_endpoint(state.db, data)
    await dbq.set_setting(state.db, "ntfy_server", "https://global.example")
    route = respx.post("https://global.example/alerts").mock(return_value=httpx.Response(200))
    outcome = await dispatch_event(state, endpoint, {"msg": "x"}, "", "")
    assert outcome["status"] == "delivered" and route.called


async def test_dispatch_without_server_fails(state, sample_endpoint_data):
    data = {**sample_endpoint_data, "ntfy_server": None}
    endpoint = await dbq.create_endpoint(state.db, data)
    outcome = await dispatch_event(state, endpoint, {"msg": "x"}, "", "")
    assert outcome["status"] == "failed" and outcome["attempts"] == 0
    assert "no ntfy server" in outcome["error"]


def test_build_notification_truncates_oversized_message(sample_endpoint_data):
    # message_template renders empty -> falls back to pretty-printed payload,
    # which must be truncated to MAX_BODY regardless of which branch produced it.
    endpoint = {**sample_endpoint_data, "message_template": "{missing}"}
    event = {"blob": "x" * (MAX_BODY * 2)}
    notif = build_notification(endpoint, event)
    assert len(notif["message"]) == MAX_BODY


@respx.mock
async def test_dispatch_unicode_title_is_delivered_and_recorded(state, sample_endpoint_data):
    data = {**sample_endpoint_data, "title_template": "Gerät ⚠ {msg}"}
    endpoint = await dbq.create_endpoint(state.db, data)
    route = respx.post("https://ntfy.example.com/alerts").mock(return_value=httpx.Response(200))
    outcome = await dispatch_event(state, endpoint, {"msg": "offline"}, "1.2.3.4", "{}")
    assert outcome["status"] == "delivered"
    sent_title = route.calls[0].request.headers["Title"]
    assert sent_title.isascii() and sent_title.startswith("=?UTF-8?B?")
    row = await dbq.get_delivery(state.db, outcome["delivery_id"])
    assert row["status"] == "delivered"
    assert row["title"] == "Gerät ⚠ offline"


@respx.mock
async def test_dispatch_latin1_only_title_is_delivered_and_recorded(state, sample_endpoint_data):
    # Title with only latin-1-range non-ASCII (no emoji/other higher codepoints) must
    # still be RFC 2047-encoded, since httpx requires strict ASCII header values.
    data = {**sample_endpoint_data, "title_template": "Gerät offline {msg}"}
    endpoint = await dbq.create_endpoint(state.db, data)
    route = respx.post("https://ntfy.example.com/alerts").mock(return_value=httpx.Response(200))
    outcome = await dispatch_event(state, endpoint, {"msg": "now"}, "1.2.3.4", "{}")
    assert outcome["status"] == "delivered"
    sent_title = route.calls[0].request.headers["Title"]
    assert sent_title.isascii() and sent_title.startswith("=?UTF-8?B?")
    row = await dbq.get_delivery(state.db, outcome["delivery_id"])
    assert row["status"] == "delivered"
    assert row["title"] == "Gerät offline now"


async def test_dispatch_records_failure_on_unexpected_exception(state, sample_endpoint_data, monkeypatch):
    endpoint = await dbq.create_endpoint(state.db, sample_endpoint_data)

    async def boom(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(ntfy, "send", boom)
    outcome = await dispatch_event(state, endpoint, {"msg": "x"}, "1.2.3.4", "{}")
    assert outcome["status"] == "failed"
    assert "ValueError" in outcome["error"]
    row = await dbq.get_delivery(state.db, outcome["delivery_id"])
    assert row["status"] == "failed"
    assert "ValueError" in row["error"]
